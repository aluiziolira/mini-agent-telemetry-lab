"""Console backend for local debugging."""

import json

from .base import TelemetryBackend


class ConsoleBackend(TelemetryBackend):
    """Console backend for local debugging."""

    def emit_span(self, span_doc: dict) -> None:
        print(f"[tracer] span: {json.dumps(span_doc, indent=2)}")
