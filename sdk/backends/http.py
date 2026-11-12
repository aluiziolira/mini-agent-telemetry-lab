"""HTTP backend for telemetry export."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx

from sdk.types import TelemetrySpanDoc

from .base import TelemetryBackend

logger = logging.getLogger("telemetry_lab")

# Keep agent execution responsive: bounded attempts inside a short wall-clock budget.
_MAX_ATTEMPTS = 3
_TOTAL_BUDGET_SECONDS = 2.5
_BASE_BACKOFF_SECONDS = 0.05
_MAX_BACKOFF_SECONDS = 0.4
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def span_idempotency_key(span_doc: TelemetrySpanDoc) -> str:
    """Stable per-span idempotency key so retries dedupe on the backend."""
    span_id = span_doc.get("span_id")
    if span_id is None:
        raise ValueError("span_doc missing span_id")
    return f"span:{span_id}"


def _parse_retry_after_seconds(value: str | None, *, now: float) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return float(stripped)
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        return None
    return max(0.0, retry_at.timestamp() - now)


class HTTPBackend(TelemetryBackend):
    """Synchronous, fail-open HTTP exporter for completed spans.

    Sends a stable Idempotency-Key per span and retries transport errors plus
    5xx/429 responses with jittered backoff, honoring Retry-After when present.
    Retry scheduling shares a time budget; each attempt uses the remaining time
    as its phase timeout. Synchronous HTTPX phase limits are not a strict
    wall-clock deadline for a whole request.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 5.0,
        *,
        max_attempts: int = _MAX_ATTEMPTS,
        total_budget_seconds: float = _TOTAL_BUDGET_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if total_budget_seconds <= 0:
            raise ValueError("total_budget_seconds must be > 0")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_attempts = max_attempts
        self.total_budget_seconds = total_budget_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng if rng is not None else random.Random()
        # Per-request timeout stays under the overall budget.
        self._request_timeout = min(timeout, total_budget_seconds)
        self.client = httpx.Client(timeout=self._request_timeout)

    def emit_span(self, span_doc: TelemetrySpanDoc) -> None:
        try:
            self._emit_with_retries(span_doc)
        except Exception as exc:
            logger.warning(
                "Telemetry export failed; continuing without blocking the agent",
                extra={
                    "span_id": span_doc.get("span_id"),
                    "trace_id": span_doc.get("trace_id"),
                    "error": str(exc),
                },
            )

    def _emit_with_retries(self, span_doc: TelemetrySpanDoc) -> None:
        idempotency_key = span_idempotency_key(span_doc)
        headers = {
            "X-API-Key": self.api_key,
            "Idempotency-Key": idempotency_key,
        }
        url = f"{self.base_url}/api/v1/ingest/span/"
        deadline = self._monotonic() + self.total_budget_seconds
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            try:
                # HTTPX timeouts are phase limits, not a hard wall-clock deadline.
                # Rebuild them for each attempt so a retry cannot inherit the
                # original configured timeout after earlier attempts consumed time.
                attempt_timeout = httpx.Timeout(min(self._request_timeout, remaining))
                response = self.client.post(
                    url, headers=headers, json=span_doc, timeout=attempt_timeout
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_attempts or not self._sleep_before_retry(
                    attempt=attempt,
                    deadline=deadline,
                    retry_after_seconds=None,
                ):
                    raise
                continue

            if response.status_code < 400:
                return

            last_error = httpx.HTTPStatusError(
                f"Client error '{response.status_code}' for url '{response.url}'",
                request=response.request,
                response=response,
            )
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                raise last_error
            if attempt >= self.max_attempts:
                raise last_error

            retry_after = _parse_retry_after_seconds(
                response.headers.get("Retry-After"),
                now=time.time(),
            )
            if not self._sleep_before_retry(
                attempt=attempt,
                deadline=deadline,
                retry_after_seconds=retry_after,
            ):
                raise last_error

        if last_error is not None:
            raise last_error
        raise TimeoutError("telemetry export exhausted time budget before any attempt")

    def _sleep_before_retry(
        self,
        *,
        attempt: int,
        deadline: float,
        retry_after_seconds: float | None,
    ) -> bool:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            return False

        exponential = min(
            _MAX_BACKOFF_SECONDS,
            _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)),
        )
        jittered = exponential * (0.5 + self._rng.random())
        delay = jittered
        if retry_after_seconds is not None:
            delay = max(delay, retry_after_seconds)
        delay = min(delay, remaining)
        if delay <= 0:
            return False
        self._sleep(delay)
        return self._monotonic() < deadline

    def close(self) -> None:
        self.client.close()
