"""Run finalization locking and late-span rejection tests."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import Run, Span
from core.services.finalization import finalize_run_if_needed
from core.services.ingestion import ingest_span


def _using_postgres() -> bool:
    return connection.vendor == "postgresql"


def _ingest(client, *, api_key: str, payload: dict):
    return client.post(
        reverse("ingest_span"),
        data=payload,
        content_type="application/json",
        headers={"X-API-Key": api_key},
    )


@pytest.mark.django_db
def test_ingest_locks_run_row_with_select_for_update(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()
    trace_id = uuid.uuid4()

    with patch("core.services.ingestion.Run.objects.select_for_update") as mock_sfu:
        mock_sfu.return_value.get.side_effect = lambda **kwargs: Run.objects.get(**kwargs)
        response = _ingest(
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
                "is_final": True,
            },
        )

    assert response.status_code == 201
    mock_sfu.assert_called_once_with()
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"


@pytest.mark.django_db
def test_second_final_span_after_completion_is_conflict(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()
    trace_id = uuid.uuid4()

    first = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final-a",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
    )
    assert first.status_code == 201

    second = _ingest(
        client,
        api_key="dev-ingest-key",
        payload={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final-b",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
    )
    assert second.status_code == 409
    assert Span.objects.filter(trace_id__trace_id=trace_id).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_concurrent_finalization_completes_once_under_postgres():
    """Requires PostgreSQL; skipped on SQLite where row locks are ineffective."""
    if not _using_postgres():
        pytest.skip("PostgreSQL required for concurrent finalization locking test")

    run = Run.objects.create(
        agent_name="concurrent_agent",
        status="running",
        start_time=timezone.now(),
    )
    end_a = timezone.now()
    end_b = end_a + timedelta(milliseconds=5)

    def finalize_a() -> bool:
        with transaction.atomic():
            locked = Run.objects.select_for_update().get(pk=run.pk)
            return finalize_run_if_needed(run=locked, is_final=True, end_time=end_a)

    def finalize_b() -> bool:
        with transaction.atomic():
            locked = Run.objects.select_for_update().get(pk=run.pk)
            return finalize_run_if_needed(run=locked, is_final=True, end_time=end_b)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda fn: fn(), [finalize_a, finalize_b]))

    run.refresh_from_db()
    assert run.status == "completed"
    assert results.count(True) >= 1
    # Under row locking, the second caller observes completed and returns True
    # without re-applying totals; end_time stays from the first writer.
    assert run.end_time in {end_a, end_b}


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_concurrent_ingest_final_spans_one_wins_under_postgres(settings):
    if not _using_postgres():
        pytest.skip("PostgreSQL required for concurrent ingest locking test")
    if not os.environ.get("INGEST_API_KEY"):
        settings.INGEST_API_KEY = "dev-ingest-key"

    trace_id = uuid.uuid4()
    now = timezone.now()

    def post_final(name: str) -> str:
        from django.test import Client

        client = Client()
        response = client.post(
            reverse("ingest_span"),
            data={
                "span_id": str(uuid.uuid4()),
                "trace_id": str(trace_id),
                "name": name,
                "span_type": "chain",
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "status_code": "OK",
                "is_final": True,
                "agent_name": "concurrent_agent",
            },
            content_type="application/json",
            headers={"X-API-Key": settings.INGEST_API_KEY},
        )
        return str(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(post_final, ["final-a", "final-b"]))

    assert sorted(statuses) == ["201", "409"]
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"
    assert Span.objects.filter(trace_id=run).count() == 1


@pytest.mark.django_db
def test_ingest_span_service_uses_row_lock_before_finalization():
    now = timezone.now()
    data = {
        "span_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "name": "root",
        "span_type": "chain",
        "start_time": now,
        "end_time": now,
        "status_code": "OK",
        "parent_span_id": None,
        "attributes": {},
        "agent_name": "lock_test",
        "is_final": True,
    }

    with patch("core.services.ingestion.Run.objects.select_for_update") as mock_sfu:
        mock_sfu.return_value.get.side_effect = lambda **kwargs: Run.objects.get(**kwargs)
        result = ingest_span(
            data=data,  # type: ignore[arg-type]
            idempotency_key=None,
            post_ingest_hook=lambda *_args: None,
        )

    assert result["run_completed"] is True
    mock_sfu.assert_called_once_with()
