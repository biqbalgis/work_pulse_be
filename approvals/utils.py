from workspaces.models import WorkspaceMember

def can_approve(request_user, target_user, workspace):
    """
    Final Rules:
     - Superuser can approve anyone.
     - If employee has manager → manager, admin, or superuser can approve.
     - If no manager → admin or superuser can approve.
    """

    # Superuser bypass
    if request_user.is_superuser:
        return True

    # Must belong to the same workspace
    approver = WorkspaceMember.objects.filter(user=request_user, workspace=workspace).first()
    employee = WorkspaceMember.objects.filter(user=target_user, workspace=workspace).first()

    if not approver or not employee:
        return False

    # CASE 1: Employee has a manager
    if employee.manager:
        # // Manager or Admin of workspace can approve
        if employee.manager == request_user or approver.role == "admin":
            return True
        return False

    # CASE 2: Employee has NO manager → Only admin can approve
    return approver.role == "admin"
