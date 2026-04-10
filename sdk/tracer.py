from __future__ import annotations

import logging
import os
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger("telemetry_lab")


class SpanContext(AbstractContextManager["SpanContext"]):
    def __init__(
        self,
        tracer: "Tracer",
        name: str,
        span_type: str,
        parent_span_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self.tracer = tracer
        self.span_id = str(uuid4())
        self.name = name
        self.span_type = span_type
        self.parent_span_id = parent_span_id
        self.attributes: dict[str, Any] = dict(attrs or {})
        self.start_time: datetime | None = None

    def __enter__(self) -> "SpanContext":
        self.start_time = datetime.now(timezone.utc)
        self.tracer._stack.append(self)
        if self.parent_span_id is None and self.tracer._root_span_id is None:
            self.tracer._root_span_id = self.span_id
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        end_time = datetime.now(timezone.utc)
        if exc is not None:
            self.attributes["error_message"] = str(exc)

        is_final = (
            self.tracer._finalize_root and self.span_id == self.tracer._root_span_id
        )
        if is_final:
            self.attributes.update(self.tracer._final_span_attrs)

        self.tracer._emit(
            {
                "span_id": self.span_id,
                "trace_id": self.tracer.run_id,
                "parent_span_id": self.parent_span_id,
                "name": self.name,
                "span_type": self.span_type,
                "start_time": self.start_time.isoformat()
                if self.start_time
                else end_time.isoformat(),
                "end_time": end_time.isoformat(),
                "status_code": "ERROR" if exc is not None else "OK",
                "attributes": self.attributes,
                "agent_name": self.tracer.agent_name,
                "is_final": is_final,
            }
        )
        self.tracer._stack.pop()
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class Tracer:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_name = "unknown"
        self.run_id = str(uuid4())
        self.timeout = float(os.getenv("TELEMETRY_TIMEOUT", "5.0"))
        self.client = httpx.Client(timeout=self.timeout)
        self._stack: list[SpanContext] = []
        self._root_span_id: str | None = None
        self._finalize_root = False
        self._final_span_attrs: dict[str, Any] = {}
        self._buffer: list[dict] = []
        self._batch_size = int(os.getenv("TELEMETRY_BATCH_SIZE", "10"))
        self._flush_interval = float(os.getenv("TELEMETRY_FLUSH_INTERVAL", "5.0"))
        self._max_retries = int(os.getenv("TELEMETRY_MAX_RETRIES", "3"))
        self._last_flush = time.time()

    def span(
        self,
        name: str,
        span_type: str,
        parent_span_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> SpanContext:
        return SpanContext(
            self, name, span_type, parent_span_id=parent_span_id, attrs=attrs
        )

    def _emit(self, span_doc: dict[str, Any]) -> None:
        self._buffer.append(span_doc)
        if (
            len(self._buffer) >= self._batch_size
            or time.time() - self._last_flush >= self._flush_interval
        ):
            self._flush_buffer()

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer = []
        self._last_flush = time.time()
        self._emit_batch_with_retry(batch)

    def _emit_batch_with_retry(self, span_docs: list[dict]) -> None:
        for attempt in range(self._max_retries + 1):
            try:
                response = self.client.post(
                    f"{self.base_url}/api/v1/ingest/span/",
                    headers={"X-API-Key": self.api_key},
                    json=span_docs[0] if len(span_docs) == 1 else span_docs,
                )
                response.raise_for_status()
                return
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    logger.warning(
                        "Client error, not retrying",
                        extra={
                            "status_code": e.response.status_code,
                            "attempt": attempt,
                        },
                    )
                    return
            except (httpx.TimeoutException, httpx.ConnectError):
                pass
            except Exception as e:
                logger.warning(
                    "Unexpected error, not retrying", extra={"error": str(e)}
                )
                return

            if attempt < self._max_retries:
                sleep_time = 0.1 * (2**attempt)
                time.sleep(sleep_time)

        logger.warning(
            "Failed to emit spans after retries",
            extra={"span_count": len(span_docs), "max_retries": self._max_retries},
        )

    def finish(self, final_span_attrs: dict[str, Any] | None = None) -> None:
        if self._stack and self._root_span_id:
            self._finalize_root = True
            self._final_span_attrs = dict(final_span_attrs or {})
            return

        self._flush_buffer()
        self._emit(
            {
                "span_id": str(uuid4()),
                "trace_id": self.run_id,
                "parent_span_id": None,
                "name": "run_finish",
                "span_type": "chain",
                "start_time": datetime.now(timezone.utc).isoformat(),
                "end_time": datetime.now(timezone.utc).isoformat(),
                "status_code": "OK",
                "attributes": dict(final_span_attrs or {}),
                "agent_name": self.agent_name,
                "is_final": True,
            }
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        self._flush_buffer()
        self.client.close()
