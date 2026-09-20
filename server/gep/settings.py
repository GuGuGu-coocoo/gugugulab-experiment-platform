import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get('GEP_DATA_DIR', BASE_DIR / 'local_data'))
SECRET_KEY = os.environ.get('GEP_SECRET_KEY', 'test-only-not-a-running-instance')
DEBUG = False
EXPERIMENT_HOST = os.environ.get('GEP_EXPERIMENT_HOST', 'experiment.localhost')
ADMIN_HOST = os.environ.get('GEP_ADMIN_HOST', 'admin.localhost')
WWW_HOST = os.environ.get('GEP_WWW_HOST', 'www.localhost')
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'admin.localhost', 'experiment.localhost', 'testserver', EXPERIMENT_HOST, ADMIN_HOST, WWW_HOST]
ROOT_URLCONF = 'gep.urls'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'core']
MIDDLEWARE = ['core.limits.RequestLimits', 'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'core.middleware.AccountGate']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'gep.sqlite3', 'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
# Server timezone for every rendered timestamp; pages label it explicitly.
TIME_ZONE = os.environ.get('GEP_TIME_ZONE', 'UTC')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_NAME = 'gep_admin'
SESSION_COOKIE_SECURE = os.environ.get('GEP_HTTPS') == '1'
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
DATA_UPLOAD_MAX_MEMORY_SIZE = 268435456
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'core.ui.context']}}]
