import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get('GEP_DATA_DIR', BASE_DIR / 'local_data'))
SECRET_KEY = os.environ.get('GEP_SECRET_KEY', 'test-only-not-a-running-instance')
DEBUG = False
EXPERIMENT_HOST = os.environ.get('GEP_EXPERIMENT_HOST', 'experiment.localhost')
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'admin.localhost', 'experiment.localhost', 'testserver', EXPERIMENT_HOST]
ROOT_URLCONF = 'gep.urls'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'core']
MIDDLEWARE = ['core.limits.RequestLimits', 'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'gep.sqlite3', 'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_NAME = 'gep_admin'
SESSION_COOKIE_SECURE = os.environ.get('GEP_HTTPS') == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
DATA_UPLOAD_MAX_MEMORY_SIZE = 268435456
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request']}}]
