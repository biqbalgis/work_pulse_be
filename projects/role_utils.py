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

from workspaces.models import WorkspaceMember

from .archive import ensure_open
from .models import JobTitle, Project, ProjectRole, UserProjectRole

DEFAULT_JOB_TITLE_NAME = "EG"


def default_job_title(workspace):
    """The workspace's default job title, created (or restored if soft-deleted) on first use."""
    job_title, _created = JobTitle.objects.get_or_restore(workspace=workspace, name=DEFAULT_JOB_TITLE_NAME)
    return job_title


def resolve_project_workspace(user, project_id):
    """The project's workspace, provided `user` is a member of it. Anyone in a workspace may log time on any of its
    projects, so membership is the only access rule; anything else is rejected."""
    project = Project.objects.select_related("workspace").filter(id=project_id).first()
    if project is None:
        raise ValidationError("Project not found.")
    ensure_open(project)  # no time on an archived project
    if not WorkspaceMember.objects.filter(user=user, workspace_id=project.workspace_id).exists():
        raise ValidationError("This project is not in your workspace.")
    return project.workspace


def resolve_entry_role(user, project_id, workspace, job_title_id=None, include_deleted_assignments=False):
    """Return (job_title_id, hourly_rate or None) for an entry of `user` on `project_id`."""
    if job_title_id is None:
        job_title_id = default_job_title(workspace).id
    elif not JobTitle.all_objects.filter(id=job_title_id, workspace=workspace).exists():
        raise ValidationError("Job title not found in this workspace.")

    assignments = UserProjectRole.all_objects if include_deleted_assignments else UserProjectRole.objects
    upr = assignments.filter(user=user, project_id=project_id, job_title_id=job_title_id).first()
    rate = upr.hourly_rate if upr else None
    if rate is None:
        project_role = ProjectRole.objects.filter(project_id=project_id, job_title_id=job_title_id).first()
        rate = project_role.hourly_rate if project_role else None
    return job_title_id, rate


def labour_cost(duration_hours, hourly_rate):
    """Labour cost of an entry; no rate means no labour cost."""
    if hourly_rate is None:
        return Decimal("0")
    return Decimal(duration_hours) * Decimal(hourly_rate)
