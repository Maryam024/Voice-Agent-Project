import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")


def _list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


DEBUG = os.environ.get("DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
if not DEBUG and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Set SECRET_KEY in the environment when DEBUG=0.")

# Production: ALLOWED_HOSTS=your-domain.com and CSRF_TRUSTED_ORIGINS=https://your-domain.com
ALLOWED_HOSTS = _list("ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = _list("CSRF_TRUSTED_ORIGINS")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # the host's proxy does the HTTPS

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "frontdesk",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves admin CSS and the built React app
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# SQLite by default. Set DATABASE_URL to use Postgres (Neon / Supabase).
DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

TIME_ZONE = os.environ.get("CLINIC_TZ", "Asia/Karachi")
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The Docker build puts the built React app here. Django then serves it at "/".
FRONTEND_DIR = BASE_DIR / "frontend_dist"
if FRONTEND_DIR.exists():
    WHITENOISE_ROOT = FRONTEND_DIR
    WHITENOISE_INDEX_FILE = True

# Project settings
CLINIC_NAME = os.environ.get("CLINIC_NAME", "Demo Clinic")
REMINDER_REPEAT_MINUTES = int(os.environ.get("REMINDER_REPEAT_MINUTES", 2))
TASK_LEAD_MINUTES = int(os.environ.get("TASK_LEAD_MINUTES", 30))

CLINIC_PHONE = os.environ.get("CLINIC_PHONE", "")
CLINIC_ADDRESS = os.environ.get("CLINIC_ADDRESS", "")

EMERGENCY_NUMBER = os.environ.get("EMERGENCY_NUMBER", "1122")