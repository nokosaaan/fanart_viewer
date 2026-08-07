import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root (one level above backend/) if present.
# This means `docker-compose restart web` is enough to pick up .env changes
# without needing to recreate the container.
try:
    from dotenv import load_dotenv
    _env_path = BASE_DIR.parent / '.env'
    if not _env_path.exists():
        _env_path = BASE_DIR / '.env'
    load_dotenv(_env_path, override=False)  # override=False: container env vars take priority
except ImportError:
    pass

_raw_secret_key = os.environ.get('DJANGO_SECRET_KEY', 'change-me')
DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

if not DEBUG and _raw_secret_key in ('change-me', '', 'dev-secret'):
    raise RuntimeError(
        'DJANGO_SECRET_KEY must be set to a strong random value in production. '
        'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
    )

SECRET_KEY = _raw_secret_key

_allowed_hosts_env = os.environ.get('DJANGO_ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()] or ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'item',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'backend.cors.SimpleCorsMiddleware',
    'security.auth_middleware.SimpleAuthMiddleware',
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
STATIC_ROOT = BASE_DIR / 'staticfiles'

# save_previews accepts full-res images as base64 JSON; the client chunks
# batches to ~60MB but the base64+JSON overhead needs headroom above that.
# Default (2.5MB) is far too small and makes Django itself 400 the request
# with RequestDataTooBig before it ever reaches the view.
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024

# Serve the built React frontend via WhiteNoise.
# In production, run `npm run build` in frontend/ first; the dist/ directory
# is mounted (or copied) into the container at /app/frontend_dist/.
_FRONTEND_DIST = Path(os.environ.get('FRONTEND_DIST', BASE_DIR.parent / 'frontend' / 'dist'))
WHITENOISE_ROOT = _FRONTEND_DIST if _FRONTEND_DIST.exists() else None
WHITENOISE_INDEX_FILE = True  # serve index.html for non-API paths (SPA support)

# Tell Django that Cloudflare terminates HTTPS — cookies and redirects use https://
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework pagination defaults
REST_FRAMEWORK = {
    # use a pagination class that allows the frontend to request `page_size`
    # (capped via `max_page_size` in the pagination class)
    'DEFAULT_PAGINATION_CLASS': 'backend.pagination.LargePageNumberPagination',
    'PAGE_SIZE': 50,
}
