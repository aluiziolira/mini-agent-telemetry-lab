"""Django settings for telemetry_lab project."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from huey.contrib.sql_huey import SqlHuey

from telemetry_lab.logging_config import get_logging_config

load_dotenv()

SUPPORTED_EVAL_LLM_PROVIDERS = {"openai"}
PLACEHOLDER_SECRET_PREFIXES = ("replace_me", "changeme")


def get_env_str(key: str, *, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(key, default)
    if value is None:
        if required:
            raise ImproperlyConfigured(f"{key} is required")
        return ""

    value = value.strip()
    if required and not value:
        raise ImproperlyConfigured(f"{key} is required")
    return value


def validate_non_placeholder_secret(key: str, value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith(PLACEHOLDER_SECRET_PREFIXES):
        raise ImproperlyConfigured(f"{key} must not use a placeholder value")
    return value


def get_env_bool(key: str, *, default: bool = False) -> bool:
    raw_value = os.environ.get(key)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ImproperlyConfigured(
        f"{key} must be a boolean string: true/false, 1/0, yes/no, or on/off"
    )


def get_allowed_hosts(debug: bool, *, require_when_not_debug: bool) -> list[str]:
    raw_value = os.environ.get("ALLOWED_HOSTS", "")
    hosts = [host.strip() for host in raw_value.split(",") if host.strip()]
    if require_when_not_debug and not debug and not hosts:
        raise ImproperlyConfigured("ALLOWED_HOSTS is required when DEBUG is False")
    return hosts


def get_database_settings() -> tuple[str, dict[str, object]]:
    database_url = get_env_str("DATABASE_URL", required=True)
    try:
        database_config = dj_database_url.parse(database_url)
    except Exception as exc:  # pragma: no cover - exact exception depends on parser
        raise ImproperlyConfigured(f"DATABASE_URL is invalid: {exc}") from exc

    if not database_config.get("ENGINE") or not database_config.get("NAME"):
        raise ImproperlyConfigured("DATABASE_URL is invalid")

    return database_url, database_config


def get_eval_llm_provider() -> str:
    provider = get_env_str("EVAL_LLM_PROVIDER", default="openai")
    if provider not in SUPPORTED_EVAL_LLM_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_EVAL_LLM_PROVIDERS))
        raise ImproperlyConfigured(f"EVAL_LLM_PROVIDER must be one of: {supported}")
    return provider


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = validate_non_placeholder_secret(
    "DJANGO_SECRET_KEY", get_env_str("DJANGO_SECRET_KEY", required=True)
)
DEBUG = get_env_bool("DEBUG", default=False)

INGEST_API_KEY = validate_non_placeholder_secret(
    "INGEST_API_KEY", get_env_str("INGEST_API_KEY", required=True)
)
DATABASE_URL, DATABASE_CONFIG = get_database_settings()
EVAL_LLM_PROVIDER = get_eval_llm_provider()
if EVAL_LLM_PROVIDER == "openai":
    LLM_API_KEY = get_env_str("LLM_API_KEY", required=True)
else:  # pragma: no cover - guarded by provider validation above
    LLM_API_KEY = get_env_str("LLM_API_KEY")
ALLOWED_HOSTS = get_allowed_hosts(DEBUG, require_when_not_debug=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "huey.contrib.djhuey",
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.request_id.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

LOGGING = get_logging_config()

ROOT_URLCONF = "telemetry_lab.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "telemetry_lab.wsgi.application"

DATABASES = {
    "default": DATABASE_CONFIG,
}

HUEY = SqlHuey("telemetry_lab", database=DATABASE_URL)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
