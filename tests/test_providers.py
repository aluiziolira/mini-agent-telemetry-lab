"""Focused tests for provider configuration and response guards."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.providers.factory import create_llm_provider
from core.providers.openai import OpenAIProvider


def test_create_llm_provider_uses_validated_django_settings(settings, monkeypatch):
    settings.EVAL_LLM_PROVIDER = "openai"
    settings.LLM_API_KEY = "settings-llm-key"
    monkeypatch.setenv("LLM_API_KEY", "raw-env-key")

    with patch("core.providers.factory.OpenAIProvider") as mock_provider:
        create_llm_provider()

    mock_provider.assert_called_once_with(api_key="settings-llm-key")


def test_create_llm_provider_rejects_unsupported_provider(settings):
    settings.EVAL_LLM_PROVIDER = "anthropic"
    settings.LLM_API_KEY = "settings-llm-key"

    with pytest.raises(ValueError, match="Unsupported provider configured: anthropic"):
        create_llm_provider()


@pytest.mark.parametrize("content", [None, "", "   "])
def test_openai_provider_raises_clear_error_for_empty_completion_content(content):
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )

    with patch("core.providers.openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        provider = OpenAIProvider(api_key="test-key")

        with pytest.raises(ValueError, match="OpenAI provider returned empty completion content"):
            provider.create_completion(messages=[{"role": "user", "content": "Hi"}])
