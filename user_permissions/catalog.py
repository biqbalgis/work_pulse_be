"""
Every permission in the app, grouped by area. A permission is "<area>.<action>", e.g. "projects.edit".

People get permissions from their permission group in the workspace (one group each), plus any extras an admin
grants or removes for that one person (user_permissions.models.UserPermission). The built-in Administrator group
has every permission, always. Superusers have every permission everywhere.

Everyone may always see and work on their OWN time, field tickets and time-off requests; the "view_all" / "_all"
permissions are about other people's. Someone set as an employee's approver (WorkspaceMember.manager) may also
review that employee's time without the approvals permissions.
"""

# (area key, area label, [(action, label, help), ...])
CATALOG = [
    ("dashboard", "Dashboard", [
        ("view", "Open the dashboard", "Their own hours and activity."),
        ("view_all", "See the whole workspace on the dashboard", "Everyone's hours, field tickets and asset costs."),
    ]),
    ("projects", "Projects & tasks", [
        ("view", "View projects", "The Projects page and each project's tasks."),
        ("add", "Add projects", "Create projects and their tasks."),
        ("edit", "Edit projects", "Change projects and their tasks."),
        ("delete", "Delete projects", "Delete projects and tasks."),
        ("archive", "Archive / restore projects", "Close a project for good, or bring it back."),
    ]),
    ("clients", "Clients", [
        ("view", "View clients", ""),
        ("add", "Add clients", ""),
        ("edit", "Edit clients", ""),
        ("delete", "Delete clients", ""),
    ]),
    ("rates", "Rates", [
        ("view", "View rates", "Equipment and crew rates."),
        ("add", "Add rates", ""),
        ("edit", "Edit rates", ""),
        ("delete", "Delete rates", ""),
    ]),
    ("team", "Users", [
        ("view", "View users", "The Team page."),
        ("add", "Add users", "Invite / create users in the workspace."),
        ("edit", "Edit users", "Change a user's details, approver and group."),
        ("delete", "Remove / deactivate users", ""),
        ("manage_permissions", "Manage permissions", "Create permission groups and change anyone's access."),
    ]),
    ("organization", "Workspace", [
        ("view", "View workspace", "The Organizations page."),
        ("edit", "Edit workspace settings", "Name, address, holidays and pay settings."),
    ]),
    ("timesheet", "Timesheets", [
        ("view", "Log their own time", "The Timesheet page for their own hours."),
        ("view_all", "View everyone's time", "The Admin Timesheet."),
        ("add_all", "Add time for others", "Enter hours on someone else's behalf."),
        ("edit_all", "Edit others' time", ""),
        ("delete_all", "Delete others' time", ""),
    ]),
    ("approvals", "Time approvals", [
        ("view", "Open approvals", "See the Approvals page (their own submitted weeks)."),
        ("view_all", "View everyone's approvals", "Including labour costs."),
        ("approve", "Approve / reject time", "For anyone in the workspace (approvers can always review their own team)."),
    ]),
    ("field_tickets", "Field tickets", [
        ("view", "Open field tickets", "See and download their own tickets."),
        ("add", "Create field tickets", ""),
        ("view_all", "View all field tickets", ""),
        ("revise", "Edit / revise field tickets", ""),
        ("delete", "Void / delete field tickets", ""),
        ("approve", "Accept / reject field tickets", ""),
        ("invoice", "Mark field tickets invoiced", ""),
        ("upload_documents", "Upload documents", "Approved LEMs, invoices and other files."),
        ("delete_documents", "Delete documents", "Remove uploaded files (issued LEMs are always kept)."),
    ]),
    ("time_off", "Time off", [
        ("view", "Request time off", "Their own requests."),
        ("view_all", "View everyone's time off", ""),
        ("approve", "Approve / reject time off", ""),
        ("manage_types", "Manage time-off types", ""),
    ]),
    ("reports", "Reports", [
        ("view", "Open reports", "The Reports page and LEM reports."),
        ("custom", "Custom report & report dashboard", ""),
        ("expert", "Expert report", "Overview and payroll."),
        ("profitability", "Profitability", "Costs and margins in the Expert report."),
        ("payroll_export", "Export payroll", "Payroll spreadsheets."),
    ]),
]

AREAS = {area: label for area, label, _ in CATALOG}
ALL_CODES = frozenset(f"{area}.{action}" for area, _label, actions in CATALOG for action, _l, _h in actions)

# The old on/off switches (UserPermission booleans / the menu), each answered by one permission now.
LEGACY_MODULE_CODES = {
    "dashboard": "dashboard.view",
    "reports": "reports.view",
    "custom_report": "reports.custom",
    "expert_report": "reports.expert",
    "timesheet": "timesheet.view",
    "admin_timesheet": "timesheet.view_all",
    "field_ticket": "field_tickets.view",
    "time_off": "time_off.view",
    "approval": "approvals.view",
    "assets": "rates.view",
    "projects": "projects.view",
    "clients": "clients.view",
    "organization": "organization.view",
    "team": "team.view",
    "lem_approval": "field_tickets.approve",
}

# The groups every workspace starts with, made from the old roles (what each role could do). Admins can change
# them, except the Administrator group, which always has everything.
ADMINISTRATOR = "Administrator"
STARTER_GROUPS = [
    # (name, old role it replaces, description, permissions)
    (ADMINISTRATOR, "admin", "Full access to everything in the workspace.", sorted(ALL_CODES)),
    ("Manager", "manager", "Runs projects and crews: everyone's time, clients, rates and reports.", [
        "dashboard.view", "dashboard.view_all",
        "projects.view",
        "clients.view", "clients.add", "clients.edit", "clients.delete",
        "rates.view", "rates.add", "rates.edit", "rates.delete",
        "team.view", "team.add", "team.edit",
        "timesheet.view", "timesheet.view_all", "timesheet.edit_all", "timesheet.delete_all",
        "approvals.view",
        "field_tickets.view", "field_tickets.add",
        "time_off.view",
        "reports.view", "reports.expert", "reports.payroll_export",
    ]),
    ("Field Manager", "field_manager", "Supervises field work: everyone's time and field tickets.", [
        "dashboard.view",
        "projects.view",
        "rates.view",
        "timesheet.view", "timesheet.view_all", "timesheet.edit_all", "timesheet.delete_all",
        "approvals.view",
        "field_tickets.view", "field_tickets.add",
        "time_off.view",
    ]),
    ("Crew", "user", "Logs their own time, field tickets and time off.", [
        "dashboard.view",
        "timesheet.view",
        "approvals.view",
        "field_tickets.view", "field_tickets.add",
        "time_off.view",
    ]),
]
# New members land in this group unless someone picks another.
DEFAULT_GROUP = "Crew"


def catalog_json():
    return [
        {
            "area": area,
            "label": label,
            "permissions": [
                {"code": f"{area}.{action}", "action": action, "label": action_label, "help": help_text}
                for action, action_label, help_text in actions
            ],
        }
        for area, label, actions in CATALOG
    ]
