"""Console backend for local debugging."""

import logging

from .base import TelemetryBackend

logger = logging.getLogger("telemetry_lab")


class ConsoleBackend(TelemetryBackend):
    """Console backend for local debugging."""

    def emit_span(self, span_doc: dict) -> None:
        attributes = span_doc.get("attributes", {})
        attribute_keys = sorted(attributes.keys()) if isinstance(attributes, dict) else []
        logger.info(
            "Tracer span emitted",
            extra={
                "trace_id": span_doc.get("trace_id"),
                "span_id": span_doc.get("span_id"),
                "extra_fields": {
                    "name": span_doc.get("name"),
                    "span_type": span_doc.get("span_type"),
                    "status_code": span_doc.get("status_code"),
                    "is_final": span_doc.get("is_final"),
                    "attribute_keys": attribute_keys,
                },
            },
        )

    def close(self) -> None:
        return None
