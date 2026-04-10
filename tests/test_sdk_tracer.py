from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from sdk.backends.http import HTTPBackend
from sdk.tracer import Tracer


def test_tracer_emits_nested_spans_before_terminal_finish_span() -> None:
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)
    tracer.agent_name = "research_analyst"

    with tracer.span("research_analyst_run", "chain") as root_span:
        with tracer.span(
            "yfinance_fetch", "tool", parent_span_id=root_span.span_id
        ) as child_span:
            child_span.set_attribute("tool_name", "yfinance_fetch")
        tracer.finish({"output": "Buy cautiously"})

    assert backend.emit_span.call_count == 3

    root_doc = backend.emit_span.call_args_list[0].args[0]
    child_doc = backend.emit_span.call_args_list[1].args[0]
    finish_doc = backend.emit_span.call_args_list[2].args[0]

    assert child_doc["parent_span_id"] == root_span.span_id
    assert root_doc["parent_span_id"] is None
    assert root_doc["span_id"] == root_span.span_id
    assert root_doc["is_final"] is False
    assert child_doc["is_final"] is False
    assert finish_doc["name"] == "run_finish"
    assert finish_doc["is_final"] is True
    assert finish_doc["attributes"]["output"] == "Buy cautiously"
    assert finish_doc["agent_name"] == "research_analyst"


def test_tracer_captures_exceptions_as_error_spans() -> None:
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)

    with pytest.raises(RuntimeError, match="boom"):
        with tracer.span("web_search", "tool"):
            raise RuntimeError("boom")

    span_doc = backend.emit_span.call_args.args[0]
    assert span_doc["status_code"] == "ERROR"
    assert span_doc["attributes"]["error_type"] == "RuntimeError"
    assert span_doc["attributes"]["error_message"] == "boom"


def test_tracer_defaults_tool_name_for_tool_spans() -> None:
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)

    with tracer.span("topic_summary", "tool"):
        pass

    span_doc = backend.emit_span.call_args.args[0]
    assert span_doc["attributes"]["tool_name"] == "topic_summary"


def test_finish_without_active_span_emits_terminal_span() -> None:
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)
    tracer.agent_name = "research_analyst"

    tracer.finish({"output": "done"})

    span_doc = backend.emit_span.call_args.args[0]
    assert span_doc["name"] == "run_finish"
    assert span_doc["span_type"] == "chain"
    assert span_doc["is_final"] is True
    assert span_doc["attributes"] == {"output": "done"}


def test_http_backend_is_fail_open_on_transport_errors() -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("down", request=request)
        )
    )
    backend = HTTPBackend("http://example.test", "test-key")
    backend.client = httpx.Client(transport=transport, timeout=1.0)

    backend.emit_span({"span_id": "span-1", "trace_id": "trace-1"})

    backend.close()
