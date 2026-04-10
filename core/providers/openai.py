"""OpenAI provider implementation."""

from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from core.types import ProviderMessage

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLM provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def create_completion(self, messages: list[ProviderMessage], **kwargs: Any) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            **kwargs,
        )
        content = response.choices[0].message.content
        if content is None or not content.strip():
            raise ValueError("OpenAI provider returned empty completion content")
        return cast(str, content)
