import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'work_pulse_be.settings')
django.setup()

from users.models import User
from workspaces.models import Workspace
from clients.models import Client
from projects.models import Project
from tasks.models import Task
from time_entries.models import TimeEntry

def seed():
    if not User.objects.filter(username='admin').exists():
        user = User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
        ws = Workspace.objects.create(name='Demo Workspace', created_by=user)
        client = Client.objects.create(workspace=ws, name='Demo Client')
        project = Project.objects.create(workspace=ws, client=client, name='Demo Project', created_by=user)
        task = Task.objects.create(project=project, name='Initial Setup')
        TimeEntry.objects.create(user=user, workspace=ws, project=project, task=task,
                                 description='Initial logged time',
                                 start_time='2025-01-01T09:00:00Z', end_time='2025-01-01T10:00:00Z',
                                 duration=3600)
        print('✅ Demo data created!')

if __name__ == '__main__':
    seed()
