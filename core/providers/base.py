"""Abstract base class for LLM providers.

This abstraction enables swapping LLM services without changing
evaluation logic, demonstrating dependency injection principles.
"""

from abc import ABC, abstractmethod
from typing import Any

from core.types import ProviderMessage


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def create_completion(self, messages: list[ProviderMessage], **kwargs: Any) -> str:
        """Create a completion with the given messages."""
        pass
