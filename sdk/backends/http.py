"""HTTP backend for telemetry export."""

from __future__ import annotations

import logging

import httpx

from .base import TelemetryBackend

logger = logging.getLogger("telemetry_lab")


class HTTPBackend(TelemetryBackend):
    """Synchronous, fail-open HTTP exporter for completed spans."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout)

    def emit_span(self, span_doc: dict) -> None:
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/ingest/span/",
                headers={"X-API-Key": self.api_key},
                json=span_doc,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "Telemetry export failed; continuing without blocking the agent",
                extra={
                    "span_id": span_doc.get("span_id"),
                    "trace_id": span_doc.get("trace_id"),
                    "error": str(exc),
                },
            )

    def close(self) -> None:
        self.client.close()
