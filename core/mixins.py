from workspaces.models import WorkspaceMember

class WorkspaceScopedViewSet:
    """
    Automatically filters all queries by the user's workspace.
    Use this as a mixin with any ModelViewSet.
    """
    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        # Superuser can see everything
        if user.is_superuser:
            return queryset

        workspace = getattr(user, 'primary_workspace', None)
        if hasattr(self.queryset.model, 'workspace') and workspace:
            return queryset.filter(workspace=workspace)

        return queryset.none()

    def perform_create(self, serializer):
        user = self.request.user
        workspace = getattr(user, 'primary_workspace', None)
        if hasattr(serializer.Meta.model, 'workspace') and workspace:
            serializer.save(workspace=workspace)
        else:
            serializer.save()
