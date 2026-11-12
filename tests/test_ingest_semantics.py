"""Characterization tests for ingestion idempotency and completion semantics."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import IdempotencyKey, Run, Span


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


@pytest.mark.django_db
def test_duplicate_idempotency_key_returns_safe_duplicate_without_second_span(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()
    now = timezone.now()

    payload = {
        "span_id": str(span_id),
        "trace_id": str(trace_id),
        "name": "root",
        "span_type": "chain",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "status_code": "OK",
    }

    first = _ingest(
        client,
        api_key="dev-ingest-key",
        payload=payload,
        idempotency_key="dup-key-1",
    )
    assert first.status_code == 201
    assert Span.objects.filter(span_id=span_id).count() == 1

    duplicate = _ingest(
        client,
        api_key="dev-ingest-key",
        payload=payload,
        idempotency_key="dup-key-1",
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {"span_id": str(span_id)}
    assert Span.objects.filter(span_id=span_id).count() == 1
    assert Span.objects.filter(trace_id__trace_id=trace_id).count() == 1


@pytest.mark.django_db
def test_idempotency_key_with_different_payload_returns_conflict(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    first_payload = {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(trace_id),
        "name": "root",
        "span_type": "chain",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "status_code": "OK",
    }
    first = _ingest(
        client,
        api_key="dev-ingest-key",
        payload=first_payload,
        idempotency_key="conflict-key-1",
    )
    assert first.status_code == 201
    span_count = Span.objects.filter(trace_id__trace_id=trace_id).count()

    conflict = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            **first_payload,
            "span_id": str(uuid.uuid4()),
            "name": "different-root",
        },
        idempotency_key="conflict-key-1",
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency key reused with a different payload"}
    assert Span.objects.filter(trace_id__trace_id=trace_id).count() == span_count


@pytest.mark.django_db
def test_stale_same_key_replay_succeeds_without_writes(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()
    payload = {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "name": "root",
        "span_type": "chain",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "status_code": "OK",
    }
    key = "stale-replay-key"
    assert (
        _ingest(client, api_key="dev-ingest-key", payload=payload, idempotency_key=key).status_code
        == 201
    )
    count_before = Span.objects.count()

    stale = {
        **payload,
        "start_time": (now - timedelta(hours=25)).isoformat(),
        "end_time": (now - timedelta(hours=25)).isoformat(),
    }
    # The timestamp is excluded from the canonical payload? It is included, so
    # replay the original semantic payload with time passing via patched clock.
    with patch("core.services.ingestion.timezone.now", return_value=now + timedelta(hours=25)):
        replay = _ingest(client, api_key="dev-ingest-key", payload=payload, idempotency_key=key)
    assert replay.status_code == 200
    assert Span.objects.count() == count_before
    assert IdempotencyKey.objects.filter(key=key).count() == 1

    stale_conflict = _ingest(client, api_key="dev-ingest-key", payload=stale, idempotency_key=key)
    assert stale_conflict.status_code == 409
    assert Span.objects.count() == count_before


@pytest.mark.django_db
def test_new_stale_payload_remains_bad_request(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    stale = timezone.now() - timedelta(hours=25)
    payload = {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "name": "root",
        "span_type": "chain",
        "start_time": stale.isoformat(),
        "end_time": stale.isoformat(),
        "status_code": "OK",
    }
    response = _ingest(
        client, api_key="dev-ingest-key", payload=payload, idempotency_key="new-stale-key"
    )
    assert response.status_code == 400
    assert Span.objects.count() == 0
    assert not IdempotencyKey.objects.filter(key="new-stale-key").exists()


@pytest.mark.django_db
def test_run_stays_running_until_final_span_arrives(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    root_span_id = uuid.uuid4()
    now = timezone.now()

    root_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(root_span_id),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
    )
    assert root_response.status_code == 201
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "running"

    non_final_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "parent_span_id": str(root_span_id),
            "name": "middle",
            "span_type": "tool",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"tool_name": "middle_tool"},
        },
    )
    assert non_final_response.status_code == 201
    run.refresh_from_db()
    assert run.status == "running"

    final_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
    )
    assert final_response.status_code == 201
    run.refresh_from_db()
    assert run.status == "completed"


@pytest.mark.django_db
def test_finalization_totals_are_stable_on_idempotent_retry(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    llm_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "synthesis",
            "span_type": "llm",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {
                "model": "gpt-4o-mini",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
    )
    assert llm_response.status_code == 201

    final_payload = {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(trace_id),
        "name": "completion",
        "span_type": "chain",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "status_code": "OK",
        "is_final": True,
    }

    first_final = _ingest(
        client,
        api_key="dev-ingest-key",
        payload=final_payload,
        idempotency_key="final-idempotency-key",
    )
    assert first_final.status_code == 201
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"
    assert run.total_tokens == 15
    assert run.total_cost == Decimal("0.0000")
    span_count_before_retry = run.spans.count()

    same_payload_retry = _ingest(
        client,
        api_key="dev-ingest-key",
        payload=final_payload,
        idempotency_key="final-idempotency-key",
    )
    assert same_payload_retry.status_code == 200
    assert same_payload_retry.json() == {
        "span_id": final_payload["span_id"],
        "run_status": "completed",
    }

    conflicting_retry = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            **final_payload,
            "attributes": {"prompt_tokens": 1000, "completion_tokens": 1000},
        },
        idempotency_key="final-idempotency-key",
    )
    assert conflicting_retry.status_code == 409
    assert conflicting_retry.json() == {"error": "idempotency key reused with a different payload"}

    run.refresh_from_db()
    assert run.status == "completed"
    assert run.total_tokens == 15
    assert run.total_cost == Decimal("0.0000")
    assert run.spans.count() == span_count_before_retry


@pytest.mark.django_db
def test_completed_run_rejects_new_non_idempotent_span_with_conflict(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    root_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
    )
    assert root_response.status_code == 201

    final_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
    )
    assert final_response.status_code == 201

    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"
    span_count_before_late_request = run.spans.count()

    late_response = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "late",
            "span_type": "tool",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"tool_name": "late_tool"},
        },
    )
    assert late_response.status_code == 409

    run.refresh_from_db()
    assert run.status == "completed"
    assert run.spans.count() == span_count_before_late_request


@pytest.mark.django_db
def test_hook_failure_does_not_block_persistence_or_success_response(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()
    now = timezone.now()

    with (
        patch("core.views.run_hook", side_effect=RuntimeError("hook broke")),
        patch("core.services.ingestion.logger") as mock_logger,
    ):
        response = _ingest(
            client,
            api_key="dev-ingest-key",
            payload={
                "span_id": str(span_id),
                "trace_id": str(trace_id),
                "name": "root",
                "span_type": "chain",
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "status_code": "OK",
            },
        )

    assert response.status_code == 201
    assert Span.objects.filter(span_id=span_id).exists()

    mock_logger.exception.assert_called_once()
    log_call = mock_logger.exception.call_args
    assert log_call.args[0] == "Post-ingest hook failed (ingestion unaffected)"
    assert log_call.kwargs["extra"] == {
        "trace_id": str(trace_id),
        "span_id": str(span_id),
        "extra_fields": {
            "failure_class": "RuntimeError",
            "failure_message": "hook broke",
        },
    }
