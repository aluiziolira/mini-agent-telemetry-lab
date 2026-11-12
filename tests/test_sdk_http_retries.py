"""HTTP exporter retry, give-up, and idempotency-key reuse tests."""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest

from sdk.backends.http import HTTPBackend, span_idempotency_key


def _backend_with_transport(
    handler: Any,
    *,
    sleeps: list[float],
    clock: dict[str, float] | None = None,
) -> HTTPBackend:
    clock_state = clock if clock is not None else {"now": 0.0}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock_state["now"] += seconds

    backend = HTTPBackend(
        "http://example.test",
        "test-key",
        timeout=1.0,
        max_attempts=3,
        total_budget_seconds=2.0,
        sleep=fake_sleep,
        monotonic=lambda: clock_state["now"],
        rng=random.Random(0),
    )
    backend.client = httpx.Client(transport=httpx.MockTransport(handler), timeout=1.0)
    return backend


def test_span_idempotency_key_is_stable_per_span_id() -> None:
    assert span_idempotency_key({"span_id": "abc"}) == "span:abc"


def test_http_backend_retries_5xx_then_succeeds_reusing_idempotency_key() -> None:
    requests: list[httpx.Request] = []
    responses = [
        httpx.Response(503, headers={"Retry-After": "0"}),
        httpx.Response(201, json={"span_id": "span-1"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    backend.emit_span({"span_id": "span-1", "trace_id": "trace-1"})
    backend.close()

    assert len(requests) == 2
    keys = [request.headers.get("Idempotency-Key") for request in requests]
    assert keys == ["span:span-1", "span:span-1"]
    assert sleeps  # backoff occurred between attempts


def test_http_backend_gives_up_after_retry_budget_and_stays_fail_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    # Must not raise to the caller.
    backend.emit_span({"span_id": "span-2", "trace_id": "trace-2"})
    backend.close()
    assert len(sleeps) == 2  # two backoffs after attempts 1 and 2


def test_http_backend_does_not_retry_client_errors() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"error": "bad"})

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    backend.emit_span({"span_id": "span-3", "trace_id": "trace-3"})
    backend.close()

    assert calls["count"] == 1
    assert sleeps == []


def test_http_backend_retries_transport_errors() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(201, json={"span_id": "span-4"})

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    backend.emit_span({"span_id": "span-4", "trace_id": "trace-4"})
    backend.close()

    assert calls["count"] == 3
    assert len(sleeps) == 2


def test_retry_timeout_uses_remaining_budget_and_stops_after_deadline() -> None:
    clock = {"now": 0.0}
    timeouts: list[float] = []
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        timeouts.append(request.extensions["timeout"]["connect"])
        clock["now"] += 0.6
        return httpx.Response(503, headers={"Retry-After": "0"})

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps, clock=clock)
    backend.total_budget_seconds = 1.0
    backend.emit_span({"span_id": "budget", "trace_id": "trace"})
    backend.close()

    assert calls["count"] == 2
    assert timeouts[0] == 1.0
    assert 0 < timeouts[1] < 0.4


def test_http_backend_honors_retry_after_delay() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(201, json={"span_id": "span-5"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    backend.emit_span({"span_id": "span-5", "trace_id": "trace-5"})
    backend.close()

    assert sleeps
    assert sleeps[0] >= 1.0


@pytest.mark.parametrize("status_code", [429, 503])
def test_retryable_status_codes_are_retried(status_code: int) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(status_code, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"span_id": "span-x"})

    sleeps: list[float] = []
    backend = _backend_with_transport(handler, sleeps=sleeps)
    backend.emit_span({"span_id": "span-x", "trace_id": "trace-x"})
    backend.close()
    assert calls["count"] == 2
