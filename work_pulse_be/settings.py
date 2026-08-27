import os
from datetime import timedelta

from decouple import config, Csv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = config('SECRET_KEY')

DEBUG = config('DEBUG', default=False, cast=bool)
# DEBUG = True
ALLOWED_HOSTS =  config("ALLOWED_HOSTS", default="*").split(",")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'core',
    'users',
    'workspaces',
    'clients',
    'projects',
    'tasks',
    'time_entries',
    'approvals',
    'time_off',
    'tags',
    'organization_asset',
    'reports',
    'user_permissions',
    'corsheaders',
    'msgraphbackend',

]

MIDDLEWARE = [
    # Must come before any middleware that reads/writes the response body,
    # so compression is applied last (Django's own recommended placement) —
    # otherwise every dashboard/report JSON response ships uncompressed.
    'django.middleware.gzip.GZipMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'core.middleware.activity_middleware.ActivityLoggingMiddleware',
    'core.middleware.error_logging_middleware.ErrorLoggingMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'work_pulse_be.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

WSGI_APPLICATION = 'work_pulse_be.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT'),
        'CONN_MAX_AGE': config('CONN_MAX_AGE', default=60, cast=int),

    }
}


AUTH_USER_MODEL = "users.User"

# Ensure Django uses email for authentication everywhere
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

# Django's own default (when this isn't set at all) is an empty list — i.e. no password
# strength rules whatsoever. Set explicitly so validate_password() (used by the
# forgot/reset/change-password endpoints) actually rejects weak passwords.
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
# TIME_ZONE = 'America/Vancouver'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'core.utils.pagination.StandardPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_RATES': {
        'forgot_password': '5/hour',
    },
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'EXCEPTION_HANDLER': 'core.utils.exception_handler.custom_exception_handler',
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Your Azure AD application keys
MSGRAPH_CLIENT_ID     = config('MSGRAPH_CLIENT_ID')
MSGRAPH_TENANT_ID     = config('MSGRAPH_TENANT_ID')
MSGRAPH_CLIENT_SECRET = config('MSGRAPH_CLIENT_SECRET')

EMAIL_BACKEND = "msgraphbackend.MSGraphBackend"

# The specific M365 mailbox you want the reset links to come from
MSGRAPH_USER_ID = config('MSGRAPH_USER_ID')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL')

# Backend's own domain (also used for CSRF_TRUSTED_ORIGINS below) — NOT the frontend.
domain = config('domain', default=None)

# Base URL of the frontend SPA (a different domain from the backend above), used to build
# links inside emails (password reset, etc.).
FRONTEND_URL = config('FRONTEND_URL', default='https://dev.workpulse.ca')
PASSWORD_RESET_TIMEOUT_MINUTES = config('PASSWORD_RESET_TIMEOUT_MINUTES', default=60, cast=int)


# CORS settings
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",  # Vite default port
    "http://127.0.0.1:5173",
    "https://envsys.workpulse.ca",
    "https://envision.workpulse.ca",

]

# Security settings for HTTPS
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
if domain:
    CSRF_TRUSTED_ORIGINS = [f'https://{domain}']
else:
    CSRF_TRUSTED_ORIGINS = ["https://envision.workpulse.ca","https://envsys.workpulse.ca","http://127.0.0.1:8000"]

CORS_ALLOW_CREDENTIALS = True

USE_X_FORWARDED_HOST = True
CORS_ALLOW_HEADERS = [  # Set the headers allowed in the requests
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'Cache-Control',
    'Last-Event-ID'
]


STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')