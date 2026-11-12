"""Ingestion boundary validation and post-commit fail-open behavior."""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import IdempotencyKey, Run, Span
from core.validators import MAX_TOTAL_TOKENS


def _ingest(client, *, api_key: str, payload: dict, idempotency_key: str | None = None):
    headers = {"X-API-Key": api_key}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return client.post(
        reverse("ingest_span"),
        data=payload,
        content_type="application/json",
        headers=headers,
    )


def _token_payload(*, trace_id, tokens, is_final=False):
    now = timezone.now().isoformat()
    return {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(trace_id),
        "name": "token boundary",
        "span_type": "chain",
        "start_time": now,
        "end_time": now,
        "status_code": "OK",
        "attributes": {"prompt_tokens": tokens},
        "is_final": is_final,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("tokens", [MAX_TOTAL_TOKENS + 1, 10**100])
def test_token_field_overflow_rejected_before_persistence(client, settings, tokens):
    settings.INGEST_API_KEY = "token-test-key"
    trace_id = uuid.uuid4()
    response = _ingest(
        client,
        api_key=settings.INGEST_API_KEY,
        payload=_token_payload(trace_id=trace_id, tokens=tokens),
        idempotency_key="oversized-token",
    )
    assert response.status_code == 400
    assert "prompt_tokens" in response.json()["error"]
    assert not Run.objects.filter(pk=trace_id).exists()
    assert not IdempotencyKey.objects.filter(key="oversized-token").exists()


@pytest.mark.django_db
def test_aggregate_overflow_rolls_back_final_span_and_key(client, settings):
    settings.INGEST_API_KEY = "aggregate-test-key"
    trace_id = uuid.uuid4()
    first = _ingest(
        client,
        api_key=settings.INGEST_API_KEY,
        payload=_token_payload(trace_id=trace_id, tokens=MAX_TOTAL_TOKENS),
    )
    assert first.status_code == 201
    response = _ingest(
        client,
        api_key=settings.INGEST_API_KEY,
        payload=_token_payload(trace_id=trace_id, tokens=1, is_final=True),
        idempotency_key="overflow-final",
    )
    assert response.status_code == 400
    assert "total_tokens" in response.json()["error"]
    run = Run.objects.get(pk=trace_id)
    assert run.status == "running"
    assert run.end_time is None
    assert run.total_tokens == 0
    assert run.spans.count() == 1
    assert not IdempotencyKey.objects.filter(key="overflow-final").exists()


@pytest.mark.django_db
def test_maximum_run_token_total_can_finalize(client, settings):
    settings.INGEST_API_KEY = "boundary-test-key"
    trace_id = uuid.uuid4()
    response = _ingest(
        client,
        api_key=settings.INGEST_API_KEY,
        payload=_token_payload(trace_id=trace_id, tokens=MAX_TOTAL_TOKENS, is_final=True),
    )
    assert response.status_code == 201
    run = Run.objects.get(pk=trace_id)
    assert run.status == "completed"
    assert run.total_tokens == MAX_TOTAL_TOKENS


@pytest.mark.django_db
def test_malformed_chain_token_attributes_return_400(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()
    span_id = uuid.uuid4()

    response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(span_id),
            "trace_id": str(uuid.uuid4()),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"prompt_tokens": "not-an-int", "completion_tokens": 1},
            "is_final": True,
        },
    )

    assert response.status_code == 400
    assert "prompt_tokens" in response.json()["error"]
    assert not Span.objects.filter(span_id=span_id).exists()


@pytest.mark.django_db
def test_boolean_token_attributes_return_400(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()

    response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "name": "tool_step",
            "span_type": "tool",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {
                "tool_name": "lookup",
                "prompt_tokens": True,
                "completion_tokens": 0,
            },
        },
    )

    assert response.status_code == 400
    assert "prompt_tokens" in response.json()["error"]


@pytest.mark.django_db
def test_metrics_failure_after_commit_still_returns_201(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    span_id = uuid.uuid4()
    now = timezone.now()

    with (
        patch(
            "core.services.ingestion.metrics.increment_spans_ingested",
            side_effect=RuntimeError("metrics backend down"),
        ),
        patch("core.services.ingestion.logger") as mock_logger,
    ):
        response = _ingest(
            client,
            api_key="dev-ingest-key",
            payload={
                "span_id": str(span_id),
                "trace_id": str(uuid.uuid4()),
                "name": "root",
                "span_type": "chain",
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "status_code": "OK",
            },
        )

    assert response.status_code == 201
    assert response.json()["span_id"] == str(span_id)
    assert Span.objects.filter(span_id=span_id).exists()
    mock_logger.exception.assert_called()
    assert mock_logger.exception.call_args.args[0] == (
        "Post-commit metrics update failed (ingestion unaffected)"
    )
