"""Pytest-safe Django settings bootstrap.

This module seeds the minimum required environment so pytest-django can
import Django settings without depending on a developer-local .env file.
"""

import importlib
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

os.environ["DATABASE_URL"] = f"sqlite:///{BASE_DIR / 'test.sqlite3'}"
os.environ.setdefault("DJANGO_SECRET_KEY", "pytest-secret-key")
os.environ.setdefault("INGEST_API_KEY", "pytest-ingest-key")
os.environ.setdefault("LLM_API_KEY", "pytest-llm-key")
os.environ.setdefault("EVAL_LLM_PROVIDER", "openai")
os.environ.setdefault("DEBUG", "True")

_settings = importlib.import_module("telemetry_lab.settings")

# Keep test runs independent from a live PostgreSQL instance.
_settings.HUEY = {
    "huey_class": "huey.MemoryHuey",
    "name": "telemetry_lab_test",
    "immediate": True,
}

for name in dir(_settings):
    if name.isupper():
        globals()[name] = getattr(_settings, name)
