# WorkPulse Backend API

A comprehensive Django REST API for multi-organization time tracking and productivity management.

## Features

- **Multi-Organization Support**: Complete tenant isolation
- **JWT Authentication**: Secure token-based authentication
- **Role-Based Access Control**: Admin, Supervisor, Member, Viewer roles
- **Time Tracking**: Real-time start/stop timer functionality
- **Reports & Analytics**: Comprehensive reporting with export capabilities
- **Team Management**: User and team management within organizations
- **Project Management**: Client and project tracking with billing
- **Time Off Management**: Leave request and approval system
- **Expense Tracking**: Project-related expense management
- **Activity Logging**: Complete audit trail of user actions

## Technology Stack

- **Django 5.2.5** - Web framework
- **Django REST Framework 3.15.2** - API framework
- **PostgreSQL** - Database
- **JWT Authentication** - Token-based auth
- **Celery** - Background task processing
- **Redis** - Caching and message broker

## Setup Instructions

### Prerequisites

- Python 3.10+
- PostgreSQL 12+
- Redis (for Celery)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd work_pulse_be
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv work_pulse_be_venv
   work_pulse_be_venv\Scripts\activate  # Windows
   # or
   source work_pulse_be_venv/bin/activate  # Linux/Mac
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Database Setup**
   - Create PostgreSQL database named `work_pulse`
   - Update database credentials in `work_pulse_be/settings.py` if needed:
     ```python
     DATABASES = {
         'default': {
             'ENGINE': 'django.db.backends.postgresql',
             'NAME': 'work_pulse',
             'USER': 'postgres',
             'PASSWORD': 'admin',
             'HOST': 'localhost',
             'PORT': '5432',
         }
     }
     ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create superuser**
   ```bash
   python manage.py shell -c "from users.models import User; from core.models import Organization; user = User.objects.create_superuser(username='admin', email='admin@workpulse.com', password='admin', first_name='Admin', last_name='User'); org = Organization.objects.create(name='Default Organization', created_by=user); from users.models import UserProfile; UserProfile.objects.create(user=user, organization=org, role='admin'); print('Superuser created successfully!')"
   ```

7. **Start development server**
   ```bash
   python manage.py runserver
   ```

The API will be available at `http://localhost:8000/api/`

## API Endpoints

### Authentication
- `POST /api/auth/token/login/` - User login
- `POST /api/auth/token/register/` - User registration
- `POST /api/auth/token/logout/` - User logout
- `POST /api/auth/token/refresh/` - Refresh JWT token

### Core Resources
- `GET/POST /api/organizations/` - Organization management
- `GET/POST /api/clients/` - Client management
- `GET/POST /api/teams/` - Team management
- `GET/POST /api/projects/` - Project management
- `GET/POST /api/tasks/` - Task management
- `GET/POST /api/time-entries/` - Time tracking
- `GET/POST /api/leave-requests/` - Time off management
- `GET/POST /api/expenses/` - Expense management
- `GET/POST /api/kiosks/` - Kiosk management

### Time Tracking
- `POST /api/time-entries/start_timer/` - Start timer
- `POST /api/time-entries/{id}/stop_timer/` - Stop timer
- `GET /api/time-entries/current_timer/` - Get current timer
- `GET /api/time-entries/timesheet/` - Get timesheet data

### Reports
- `GET /api/reports/time-summary/` - Time summary report
- `GET /api/reports/utilization/` - Utilization report
- `GET /api/reports/billing/` - Billing report
- `GET /api/reports/export/` - Export reports (CSV/PDF)

## User Roles

- **Admin**: Full control over organization
- **Supervisor**: Approve timesheets, view team reports
- **Member**: Add/edit personal time entries
- **Viewer**: Read-only access

## Environment Variables

Create a `.env` file for production settings:

```env
SECRET_KEY=your-secret-key
DEBUG=False
ALLOWED_HOSTS=your-domain.com
DATABASE_URL=postgresql://user:password@localhost:5432/work_pulse
REDIS_URL=redis://localhost:6379/0
```

## Development

### Running Tests
```bash
python manage.py test
```

### Code Style
The project follows PEP 8 standards. Use black for code formatting:
```bash
pip install black
black .
```

### Database Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

## Production Deployment

1. Set `DEBUG=False` in settings
2. Configure proper database credentials
3. Set up Redis for Celery
4. Configure static file serving
5. Set up proper logging
6. Use environment variables for sensitive data

## API Documentation

Once the server is running, you can access the browsable API at:
- `http://localhost:8000/api/` - API root
- `http://localhost:8000/admin/` - Django admin interface

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

This project is licensed under the MIT License.
