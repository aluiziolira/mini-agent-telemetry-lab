"""Contract tests for fail-fast startup configuration validation."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SETTINGS_SCRIPT = textwrap.dedent(
    """
    import importlib
    import json
    import sys

    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False

    module_name = sys.argv[1]
    sys.modules.pop(module_name, None)

    try:
        settings = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - exercised via subprocess
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }))
    else:
        print(json.dumps({
            "ok": True,
            "DEBUG": settings.DEBUG,
            "ALLOWED_HOSTS": getattr(settings, "ALLOWED_HOSTS", None),
            "EVAL_LLM_PROVIDER": getattr(settings, "EVAL_LLM_PROVIDER", None),
        }))
    """
)
ENV_KEYS = {
    "ALLOWED_HOSTS",
    "DATABASE_URL",
    "DEBUG",
    "DJANGO_SECRET_KEY",
    "EVAL_LLM_PROVIDER",
    "INGEST_API_KEY",
    "LLM_API_KEY",
}


def import_settings_module(module_name, **overrides):
    env = os.environ.copy()
    for key in ENV_KEYS:
        env.pop(key, None)

    env.update(overrides)

    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not pythonpath else f"{REPO_ROOT}:{pythonpath}"

    command = [sys.executable, "-c", SETTINGS_SCRIPT, module_name]
    if shutil.which("uv"):
        command = ["uv", "run", "python", "-c", SETTINGS_SCRIPT, module_name]

    completed = subprocess.run(
        command,
        cwd="/tmp",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    stdout = completed.stdout.strip().splitlines()
    if not stdout:
        raise AssertionError(f"No JSON output received. stderr={completed.stderr!r}")

    return json.loads(stdout[-1])


def test_pytest_safe_settings_bootstrap_imports_without_local_env():
    result = import_settings_module("telemetry_lab.test_settings")

    assert result["ok"] is True
    assert result["EVAL_LLM_PROVIDER"] == "openai"


def test_mypy_django_stubs_uses_pytest_safe_settings_module():
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

    assert (
        pyproject["tool"]["django-stubs"]["django_settings_module"] == "telemetry_lab.test_settings"
    )


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        (
            {"DATABASE_URL": "sqlite:////tmp/contracts.sqlite3", "INGEST_API_KEY": "test-ingest"},
            "DJANGO_SECRET_KEY",
        ),
        (
            {
                "DATABASE_URL": "sqlite:////tmp/contracts.sqlite3",
                "DJANGO_SECRET_KEY": "test-secret",
            },
            "INGEST_API_KEY",
        ),
        ({"DJANGO_SECRET_KEY": "test-secret", "INGEST_API_KEY": "test-ingest"}, "DATABASE_URL"),
        (
            {
                "DATABASE_URL": "not-a-database-url",
                "DJANGO_SECRET_KEY": "test-secret",
                "INGEST_API_KEY": "test-ingest",
            },
            "DATABASE_URL",
        ),
    ],
)
def test_required_startup_configuration_fails_fast(overrides, expected_fragment):
    result = import_settings_module("telemetry_lab.settings", **overrides)

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert expected_fragment in result["message"]


def test_debug_defaults_to_false_when_env_is_missing():
    result = import_settings_module(
        "telemetry_lab.settings",
        DATABASE_URL="sqlite:////tmp/contracts.sqlite3",
        DJANGO_SECRET_KEY="test-secret",
        INGEST_API_KEY="test-ingest",
        ALLOWED_HOSTS="localhost",
        LLM_API_KEY="test-llm-key",
    )

    assert result["ok"] is True
    assert result["DEBUG"] is False


def test_invalid_debug_value_is_rejected():
    result = import_settings_module(
        "telemetry_lab.settings",
        DATABASE_URL="sqlite:////tmp/contracts.sqlite3",
        DJANGO_SECRET_KEY="test-secret",
        INGEST_API_KEY="test-ingest",
        ALLOWED_HOSTS="localhost",
        DEBUG="sometimes",
    )

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert "DEBUG" in result["message"]


def test_debug_false_requires_allowed_hosts():
    result = import_settings_module(
        "telemetry_lab.settings",
        DATABASE_URL="sqlite:////tmp/contracts.sqlite3",
        DJANGO_SECRET_KEY="test-secret",
        INGEST_API_KEY="test-ingest",
        DEBUG="false",
        ALLOWED_HOSTS="",
        LLM_API_KEY="test-llm-key",
    )

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert "ALLOWED_HOSTS" in result["message"]


def test_openai_provider_requires_llm_api_key():
    result = import_settings_module(
        "telemetry_lab.settings",
        DATABASE_URL="sqlite:////tmp/contracts.sqlite3",
        DJANGO_SECRET_KEY="test-secret",
        INGEST_API_KEY="test-ingest",
        ALLOWED_HOSTS="localhost",
        EVAL_LLM_PROVIDER="openai",
    )

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert "LLM_API_KEY" in result["message"]


def test_unsupported_eval_llm_provider_is_rejected():
    result = import_settings_module(
        "telemetry_lab.settings",
        DATABASE_URL="sqlite:////tmp/contracts.sqlite3",
        DJANGO_SECRET_KEY="test-secret",
        INGEST_API_KEY="test-ingest",
        ALLOWED_HOSTS="localhost",
        EVAL_LLM_PROVIDER="anthropic",
        LLM_API_KEY="test-llm-key",
    )

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert "EVAL_LLM_PROVIDER" in result["message"]


@pytest.mark.parametrize(
    "key_name",
    ["DJANGO_SECRET_KEY", "INGEST_API_KEY"],
)
def test_placeholder_values_are_rejected_for_required_secrets(key_name):
    env = {
        "DATABASE_URL": "sqlite:////tmp/contracts.sqlite3",
        "DJANGO_SECRET_KEY": "test-secret",
        "INGEST_API_KEY": "test-ingest",
        "ALLOWED_HOSTS": "localhost",
        "LLM_API_KEY": "test-llm-key",
        key_name: "REPLACE_ME_value",
    }
    result = import_settings_module("telemetry_lab.settings", **env)

    assert result["ok"] is False
    assert result["error_type"] == "ImproperlyConfigured"
    assert key_name in result["message"]
