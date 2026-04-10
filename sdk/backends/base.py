"""Abstract base class for telemetry backends."""

from abc import ABC, abstractmethod
from typing import Any


class TelemetryBackend(ABC):
    """Boundary for span export implementations."""

    @abstractmethod
    def emit_span(self, span_doc: dict[str, Any]) -> None:
        """Emit one completed span document."""

    def close(self) -> None:
        """Release backend resources if needed."""
