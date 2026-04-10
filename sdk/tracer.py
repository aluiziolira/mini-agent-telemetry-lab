from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx


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
        self.client = httpx.Client(timeout=5.0)
        self._stack: list[SpanContext] = []
        self._root_span_id: str | None = None
        self._finalize_root = False
        self._final_span_attrs: dict[str, Any] = {}

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
        try:
            self.client.post(
                f"{self.base_url}/api/v1/ingest/span/",
                headers={"X-API-Key": self.api_key},
                json=span_doc,
            ).raise_for_status()
        except Exception:
            print(f"[tracer] warn: failed to emit span {span_doc['name']}")

    def finish(self, final_span_attrs: dict[str, Any] | None = None) -> None:
        if self._stack and self._root_span_id:
            self._finalize_root = True
            self._final_span_attrs = dict(final_span_attrs or {})
            return

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
