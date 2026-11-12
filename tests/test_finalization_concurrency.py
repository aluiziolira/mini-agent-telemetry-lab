"""Run finalization locking and late-span rejection tests."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, connections
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
@pytest.mark.parametrize("late_is_final", [True, False])
def test_finalization_serializes_competing_ingest_under_postgres(settings, late_is_final):
    if not _using_postgres():
        pytest.skip("PostgreSQL required for concurrent ingest locking test")
    settings.INGEST_API_KEY = "concurrency-test-key"
    now = timezone.now()
    run = Run.objects.create(agent_name="concurrent_agent", status="running", start_time=now)
    first_locked = Event()
    release_first = Event()
    second_connected = Event()
    second_pid = []

    def held_finalize(**kwargs):
        # Called by ingestion after acquiring the run row lock and writing its span.
        first_locked.set()
        assert release_first.wait(10), "first ingest was not released"
        return finalize_run_if_needed(**kwargs)

    def post_span(name, is_final):
        from django.test import Client

        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                if name == "late":
                    cursor.execute("SELECT pg_backend_pid()")
                    second_pid.append(cursor.fetchone()[0])
                    second_connected.set()
            payload = {
                "span_id": str(uuid.uuid4()),
                "trace_id": str(run.pk),
                "name": name,
                "span_type": "chain",
                "status_code": "OK",
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "attributes": {"prompt_tokens": 7 if name == "first" else 99},
                "is_final": is_final,
            }
            return _ingest(Client(), api_key=settings.INGEST_API_KEY, payload=payload).status_code
        finally:
            connections.close_all()

    with patch("core.services.ingestion.finalize_run_if_needed", side_effect=held_finalize):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(post_span, "first", True)
            try:
                assert first_locked.wait(10), "first ingest did not acquire the run lock"
                second = pool.submit(post_span, "late", late_is_final)
                assert second_connected.wait(10), "second ingest did not connect"
                deadline = time.monotonic() + 5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [second_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    assert time.monotonic() < deadline, (
                        "second ingest did not block on the row lock"
                    )
                    time.sleep(0.01)
            finally:
                release_first.set()
            assert first.result(timeout=10) == 201
            assert second.result(timeout=10) == 409

    run.refresh_from_db()
    assert run.status == "completed"
    assert run.end_time == now
    assert run.total_tokens == 7
    assert run.total_cost == Decimal("0.0000")  # rounded to four decimal places
    assert list(run.spans.values_list("name", flat=True)) == ["first"]


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
