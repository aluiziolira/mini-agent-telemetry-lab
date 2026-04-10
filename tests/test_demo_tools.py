from unittest.mock import Mock

import pytest

from scripts.demo_tools import run_tool_with_retries
from sdk.tracer import Tracer


def test_run_tool_with_retries_emits_error_attempt_before_success():
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)

    attempts = {"count": 0}

    def flaky_operation(span):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("temporary failure")
        span.set_attribute("output", {"status": "ok"})
        return {"status": "ok"}

    with tracer.span("root", "chain") as root_span:
        result = run_tool_with_retries(
            tracer,
            tool_name="web_search",
            parent_span_id=root_span.span_id,
            operation=flaky_operation,
            max_attempts=2,
        )
        tracer.finish({"output": "done"})

    assert result == {"status": "ok"}
    assert backend.emit_span.call_count == 4

    root_doc = backend.emit_span.call_args_list[0].args[0]
    first_attempt = backend.emit_span.call_args_list[1].args[0]
    second_attempt = backend.emit_span.call_args_list[2].args[0]

    assert root_doc["name"] == "root"
    assert first_attempt["name"] == "web_search"
    assert first_attempt["status_code"] == "ERROR"
    assert first_attempt["attributes"]["attempt"] == 1
    assert first_attempt["attributes"]["max_attempts"] == 2
    assert first_attempt["attributes"]["error_message"] == "temporary failure"
    assert second_attempt["name"] == "web_search"
    assert second_attempt["status_code"] == "OK"
    assert second_attempt["attributes"]["attempt"] == 2
    assert second_attempt["attributes"]["retry_count"] == 1


def test_run_tool_with_retries_rejects_invalid_attempt_count():
    backend = Mock()
    tracer = Tracer("http://example.test", "test-key", backend=backend)

    with tracer.span("root", "chain") as root_span:
        with pytest.raises(ValueError, match="max_attempts"):
            run_tool_with_retries(
                tracer,
                tool_name="web_search",
                parent_span_id=root_span.span_id,
                operation=lambda span: None,
                max_attempts=0,
            )
