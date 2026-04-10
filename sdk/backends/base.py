"""Abstract base class for telemetry backends."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class TelemetryBackend(ABC):
    """Abstract base class for telemetry backends."""

    @abstractmethod
    def emit_span(self, span_doc: Dict[str, Any]) -> None:
        """Emit a span document to the backend."""
        pass
