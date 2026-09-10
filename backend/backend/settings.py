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

# Database — Postgres by default (docker-compose dev/prod, unchanged), or
# SQLite when DB_ENGINE=sqlite3 is set — kept as the same opt-in toggle the
# standalone exe build uses, so a backup taken in one SQLite mode can be
# restored in the other and .env can flip back to Postgres if needed.
# Default stays 'postgresql' so nothing changes unless this is opted into.
if os.environ.get('DB_ENGINE', 'postgresql') == 'sqlite3':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            # Same `data/` directory tagger.py's _data_dir() already uses
            # for model files, and already a volume-mounted directory in
            # docker-compose.prod.yml so it survives container recreation.
            'NAME': os.environ.get('SQLITE_PATH', str(BASE_DIR / 'data' / 'db.sqlite3')),
            # Unlike the exe build (poller runs in-process, single writer),
            # docker-compose.prod.yml runs `web` (gunicorn) and `poller` as
            # separate OS processes against the same file — raise Python's
            # sqlite3 busy-wait so a lock held by one of them makes the
            # other retry for a while instead of immediately raising
            # "database is locked".
            'OPTIONS': {'timeout': 20},
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

if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    # WAL lets one writer and any number of readers proceed concurrently
    # (the default rollback-journal mode blocks all readers during a write)
    # — the multi-process concern above applies to reads just as much as
    # writes, since `web` and `poller` are never the same process here.
    from django.db.backends.signals import connection_created

    def _set_sqlite_pragmas(sender, connection, **kwargs):
        if connection.vendor == 'sqlite':
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA journal_mode=WAL;')
                cursor.execute('PRAGMA busy_timeout=20000;')

    connection_created.connect(_set_sqlite_pragmas)

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
