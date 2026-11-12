"""Who may run which report, and on which workspaces' data (permissions: user_permissions.catalog, "reports.*").

    report_workspaces(user, code)  -> None (superuser: every workspace) or the workspace ids where they have it
    scope(queryset, user, code)    -> the queryset limited to those workspaces
    require_some(user, code)       -> 403 unless they have it in at least one workspace
    require_project(user, project, code) -> 403 unless they have it in the project's workspace
"""

from rest_framework.exceptions import PermissionDenied

from user_permissions.access import has_perm, workspaces_with

MESSAGES = {
    "reports.view": "You don't have permission to open reports.",
    "reports.payroll_export": "You don't have permission to see payroll.",
    "reports.expert": "You don't have access to the Expert report.",
    "reports.profitability": "You don't have permission to see profitability.",
    "dashboard.view_all": "You don't have permission to see the whole workspace on the dashboard.",
}


def report_workspaces(user, code):
    if user.is_superuser:
        return None
    return set(workspaces_with(user, code) or set())


def scope(queryset, user, code, field="workspace_id"):
    workspaces = report_workspaces(user, code)
    return queryset if workspaces is None else queryset.filter(**{f"{field}__in": workspaces})


def require_some(user, code):
    if report_workspaces(user, code) == set():
        raise PermissionDenied(MESSAGES.get(code, "You don't have permission to do that."))


def require_project(user, project, code):
    if not has_perm(user, project.workspace_id, code):
        raise PermissionDenied(MESSAGES.get(code, "You don't have permission to do that."))
