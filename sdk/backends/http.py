"""HTTP backend for telemetry export."""

import httpx

from .base import TelemetryBackend


class HTTPBackend(TelemetryBackend):
    """HTTP backend for telemetry export."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout)

    def emit_span(self, span_doc: dict) -> None:
        try:
            self.client.post(
                f"{self.base_url}/api/v1/ingest/span/",
                headers={"X-API-Key": self.api_key},
                json=span_doc,
            ).raise_for_status()
        except Exception:
            pass
