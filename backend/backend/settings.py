import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me')

DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'item',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

# Database (Postgres by env)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'fanart'),
        'USER': os.environ.get('POSTGRES_USER', 'fanart'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'password'),
        'HOST': os.environ.get('DATABASE_HOST', 'db'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework pagination defaults
REST_FRAMEWORK = {
    # use a pagination class that allows the frontend to request `page_size`
    # (capped via `max_page_size` in the pagination class)
    'DEFAULT_PAGINATION_CLASS': 'backend.pagination.LargePageNumberPagination',
    'PAGE_SIZE': 50,
}

# CORS configuration
# Allow origins from environment variable `CORS_ALLOWED_ORIGINS`, comma-separated.
# If not provided and DEBUG is True, allow localhost development origins.
_cors_env = os.environ.get('CORS_ALLOWED_ORIGINS')
if _cors_env:
    # split by comma and strip whitespace
    CORS_ALLOWED_ORIGINS = [u.strip() for u in _cors_env.split(',') if u.strip()]
else:
    if DEBUG:
        CORS_ALLOWED_ORIGINS = [
            'http://localhost:3000',
            'http://127.0.0.1:3000',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
        ]
    else:
        CORS_ALLOWED_ORIGINS = []

# Convenience: allow all origins when explicitly requested (only use for testing)
if os.environ.get('CORS_ALLOW_ALL_ORIGINS', '') in ('1', 'true', 'True'):
    CORS_ALLOW_ALL_ORIGINS = True
