"""Factory for creating LLM providers."""

from django.conf import settings

from .base import LLMProvider
from .openai import OpenAIProvider


def create_llm_provider() -> LLMProvider:
    """Factory for LLM providers based on settings."""
    provider_name: str = settings.EVAL_LLM_PROVIDER

    if provider_name == "openai":
        return OpenAIProvider(api_key=settings.LLM_API_KEY)

    raise ValueError(f"Unsupported provider configured: {provider_name}")
