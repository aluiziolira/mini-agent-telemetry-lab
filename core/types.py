from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict
from uuid import UUID

SpanType = Literal["llm", "tool", "chain"]
SpanStatusCode = Literal["OK", "ERROR"]
SpanAttributes = dict[str, Any]
ProviderMessage = dict[str, str]


class SpanIngestData(TypedDict):
    span_id: UUID
    trace_id: UUID
    name: str
    span_type: SpanType
    start_time: datetime
    end_time: datetime
    status_code: SpanStatusCode
    parent_span_id: UUID | None
    attributes: SpanAttributes
    agent_name: str
    is_final: bool


class IngestSpanResult(TypedDict):
    span_id: str
    run_completed: bool


class PostIngestPayload(TypedDict):
    span_id: str
    trace_id: str
