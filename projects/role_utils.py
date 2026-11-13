"""
Default role / rate resolution for time entries.

Users are no longer assigned to projects: everyone in a workspace can log time against every active project of
that workspace, and the forms no longer ask for a project role. Every entry still carries a job title and an
hourly rate (kept on the data for reports and for a rate source later), resolved here:

  job title  the one sent by the caller, if any; otherwise the workspace's default job title (DEFAULT_JOB_TITLE_NAME)
  rate       the user's own rate for that project + job title (UserProjectRole), else the project's rate for the
             job title (ProjectRole), else None — an entry without a rate simply has no labour cost. Reports that
             price work (Report Dashboard "Amount") use the entry's assets instead.

Existing ProjectRole / UserProjectRole rows are kept and still honoured when present.
"""

from decimal import Decimal

from rest_framework.exceptions import ValidationError

from core.utils.request_memo import remember
from core.utils.workspace_utils import is_member

from .models import JobTitle, Project, ProjectRole, UserProjectRole

DEFAULT_JOB_TITLE_NAME = "EG"


def default_job_title(workspace):
    """The workspace's default job title, created (or restored if soft-deleted) on first use."""
    # memoised per request: every row of a ticket asks for the same workspace's default title
    return remember(("default_job_title", str(workspace.pk)), lambda: JobTitle.objects.get_or_restore(
        workspace=workspace, name=DEFAULT_JOB_TITLE_NAME)[0])


def resolve_project_workspace(user, project_id):
    """The project's workspace, provided `user` is a member of it. Anyone in a workspace may log time on any of its
    projects, so membership is the only access rule; anything else is rejected."""
    project = remember(
        ("project_with_workspace", str(project_id)),
        lambda: Project.objects.select_related("workspace").filter(id=project_id).first(),
    )
    if project is None:
        raise ValidationError("Project not found.")
    if not is_member(user, project.workspace_id):
        raise ValidationError("This project is not in your workspace.")
    return project.workspace


def resolve_entry_role(user, project_id, workspace, job_title_id=None, include_deleted_assignments=False):
    """Return (job_title_id, hourly_rate or None) for an entry of `user` on `project_id`."""
    if job_title_id is None:
        job_title_id = default_job_title(workspace).id
    elif not remember(
        ("job_title_in_workspace", str(job_title_id), str(workspace.pk)),
        lambda: JobTitle.all_objects.filter(id=job_title_id, workspace=workspace).exists(),
    ):
        raise ValidationError("Job title not found in this workspace.")

    # One query per (project, job title) loads every employee's own rate, and one more the project's rate, instead
    # of two queries for each employee on the ticket. (user_project_roles is unique per user+project+job title.)
    assignments = UserProjectRole.all_objects if include_deleted_assignments else UserProjectRole.objects
    own_rates = remember(
        ("own_rates", include_deleted_assignments, str(project_id), str(job_title_id)),
        lambda: {a.user_id: a.hourly_rate for a in assignments.filter(project_id=project_id, job_title_id=job_title_id)},
    )
    rate = own_rates.get(getattr(user, "pk", user))
    if rate is None:
        project_rate = remember(
            ("project_rate", str(project_id), str(job_title_id)),
            lambda: next(iter(
                ProjectRole.objects.filter(project_id=project_id, job_title_id=job_title_id)
                .values_list("hourly_rate", flat=True)[:1]
            ), None),
        )
        rate = project_rate
    return job_title_id, rate


def labour_cost(duration_hours, hourly_rate):
    """Labour cost of an entry; no rate means no labour cost."""
    if hourly_rate is None:
        return Decimal("0")
    return Decimal(duration_hours) * Decimal(hourly_rate)
