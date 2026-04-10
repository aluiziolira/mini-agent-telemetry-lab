"""Factory for creating LLM providers."""

import os

from .base import LLMProvider
from .openai import OpenAIProvider


def create_llm_provider() -> LLMProvider:
    """Factory for LLM providers based on settings."""
    provider_name = os.environ.get("EVAL_LLM_PROVIDER", "openai")

    if provider_name == "openai":
        return OpenAIProvider(api_key=os.environ["LLM_API_KEY"])
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
