from __future__ import annotations

import os
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sdk.backends.base import TelemetryBackend
from sdk.backends.http import HTTPBackend


DEFAULT_TIMEOUT_SECONDS = 5.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        self.start_time = _utc_now()
        self.tracer._enter_span(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        end_time = _utc_now()
        if exc is not None:
            self.attributes["error_type"] = exc.__class__.__name__
            self.attributes["error_message"] = str(exc)
        try:
            self.tracer._emit_completed_span(self, end_time=end_time, error=exc)
        finally:
            self.tracer._exit_span(self)
            self.tracer._flush_completed_spans()
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class Tracer:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        backend: TelemetryBackend | None = None,
    ) -> None:
        self.agent_name = "unknown"
        self.run_id = str(uuid4())
        self.timeout = float(
            os.getenv("TELEMETRY_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        )
        self.backend = backend or HTTPBackend(base_url, api_key, timeout=self.timeout)
        self._stack: list[SpanContext] = []
        self._pending_spans: dict[str, dict[str, Any]] = {}
        self._emitted_span_ids: set[str] = set()
        self._root_span_id: str | None = None
        self._final_span_attrs: dict[str, Any] | None = None

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

    def _enter_span(self, span: SpanContext) -> None:
        self._stack.append(span)
        if self._root_span_id is None:
            self._root_span_id = span.span_id

    def _exit_span(self, span: SpanContext) -> None:
        if self._stack and self._stack[-1] is span:
            self._stack.pop()
        elif span in self._stack:
            self._stack.remove(span)

    def _build_span_doc(
        self,
        span: SpanContext,
        *,
        end_time: datetime,
        error: BaseException | None,
        is_final: bool = False,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(span.attributes)
        if attributes:
            payload.update(attributes)
        if (
            span.span_type == "tool"
            and "tool_name" not in payload
            and "error_message" not in payload
        ):
            payload["tool_name"] = span.name

        return {
            "span_id": span.span_id,
            "trace_id": self.run_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "span_type": span.span_type,
            "start_time": span.start_time.isoformat()
            if span.start_time
            else end_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status_code": "ERROR" if error is not None else "OK",
            "attributes": payload,
            "agent_name": self.agent_name,
            "is_final": is_final,
        }

    def _emit_completed_span(
        self,
        span: SpanContext,
        *,
        end_time: datetime,
        error: BaseException | None,
    ) -> None:
        self._pending_spans[span.span_id] = self._build_span_doc(
            span,
            end_time=end_time,
            error=error,
        )

    def _emit_terminal_span(self) -> None:
        now = _utc_now()
        self.backend.emit_span(
            {
                "span_id": str(uuid4()),
                "trace_id": self.run_id,
                "parent_span_id": None,
                "name": "run_finish",
                "span_type": "chain",
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "status_code": "OK",
                "attributes": dict(self._final_span_attrs or {}),
                "agent_name": self.agent_name,
                "is_final": True,
            }
        )

    def _reset_run_state(self) -> None:
        self._pending_spans = {}
        self._emitted_span_ids = set()
        self._root_span_id = None
        self._final_span_attrs = None

    def _flush_completed_spans(self) -> None:
        while self._pending_spans:
            ready_span_ids = [
                span_id
                for span_id, span_doc in self._pending_spans.items()
                if span_doc["parent_span_id"] is None
                or span_doc["parent_span_id"] in self._emitted_span_ids
            ]
            if not ready_span_ids:
                break

            for span_id in ready_span_ids:
                span_doc = self._pending_spans.pop(span_id)
                self.backend.emit_span(span_doc)
                self._emitted_span_ids.add(span_id)

        if self._stack:
            return

        if self._final_span_attrs is not None and not self._pending_spans:
            self._emit_terminal_span()
            self._reset_run_state()
        elif not self._pending_spans:
            self._reset_run_state()

    def finish(self, final_span_attrs: dict[str, Any] | None = None) -> None:
        if self._stack:
            self._final_span_attrs = dict(final_span_attrs or {})
            return

        self._final_span_attrs = dict(final_span_attrs or {})
        self._emit_terminal_span()
        self._reset_run_state()

    def shutdown(self, timeout: float = 5.0) -> None:
        self.backend.close()
