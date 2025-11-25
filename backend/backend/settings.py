import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me')

# DJANGO_DEBUG is expected to be '1' (True) or '0' (False). Default to True for
# local development unless explicitly set to '0'.
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
# Fallback middleware to ensure CORS header on responses when corsheaders
# didn't set it (safety net for some proxy or 404 cases).
MIDDLEWARE.append('backend.middleware.EnsureCorsHeaderMiddleware')

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

# Database configuration
# Prefer a full DATABASE_URL (as provided by Render managed DB). Fall back to
# separate POSTGRES_* env vars for local docker-compose setups.
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    try:
        import dj_database_url
        DATABASES = {
            'default': dj_database_url.parse(
                DATABASE_URL,
                conn_max_age=600,
                ssl_require=True,
            )
        }
    except Exception:
        # If dj_database_url is not available for some reason, provide a minimal
        # parsed fallback. This is unlikely since requirements include it.
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.environ.get('POSTGRES_DB', 'fanart'),
                'USER': os.environ.get('POSTGRES_USER', 'fanart'),
                'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
                'HOST': os.environ.get('DATABASE_HOST', 'db'),
                'PORT': os.environ.get('DATABASE_PORT', '5432'),
            }
        }
else:
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
    # split by comma and strip whitespace and trailing slashes so origins match
    # browser-origin format (scheme + host + optional :port), e.g. https://site.com
    CORS_ALLOWED_ORIGINS = [u.strip().rstrip('/') for u in _cors_env.split(',') if u.strip()]
else:
    if DEBUG:
        CORS_ALLOWED_ORIGINS = [
            'https://fanart-viewer-frontend.onrender.com',
        ]
    else:
        CORS_ALLOWED_ORIGINS = []

# Convenience: allow all origins when explicitly requested (only use for testing)
if os.environ.get('CORS_ALLOW_ALL_ORIGINS', '') in ('1', 'true', 'True'):
    CORS_ALLOW_ALL_ORIGINS = True
