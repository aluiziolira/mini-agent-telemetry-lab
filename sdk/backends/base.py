"""Abstract base class for telemetry backends."""

from abc import ABC, abstractmethod

from sdk.types import TelemetrySpanDoc


class TelemetryBackend(ABC):
    """Boundary for span export implementations."""

    @abstractmethod
    def emit_span(self, span_doc: TelemetrySpanDoc) -> None:
        """Emit one completed span document."""

    def close(self) -> None:
        """Release backend resources if needed."""
