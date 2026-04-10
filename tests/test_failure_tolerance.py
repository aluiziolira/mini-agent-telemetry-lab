"""Failure-tolerance tests that prove the system degrades predictably."""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Evaluation, Run, Span
from core.tasks import evaluate_run


@pytest.mark.django_db
def test_ingestion_rejects_invalid_api_key_without_side_effects(client, settings):
    settings.INGEST_API_KEY = "correct-key"
    now = timezone.now()

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "name": "test",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}


@pytest.mark.django_db
def test_ingestion_rejects_malformed_attributes_before_persistence(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "name": "test",
            "span_type": "llm",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": "not a dict",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_duplicate_span_id_is_rejected_cleanly(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    span_id = uuid.uuid4()
    now = timezone.now()

    response1 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(span_id),
            "trace_id": str(uuid.uuid4()),
            "name": "test",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response1.status_code == 201

    response2 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(span_id),
            "trace_id": str(uuid.uuid4()),
            "name": "test2",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response2.status_code == 400
    assert response2.json() == {"error": "span_id already exists"}


@pytest.mark.django_db
def test_error_spans_do_not_prevent_run_completion(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    response1 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response1.status_code == 201

    response2 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "failing_tool",
            "span_type": "tool",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "ERROR",
            "attributes": {"error_message": "Connection timeout"},
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response2.status_code == 201

    response3 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "completion",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response3.status_code == 201

    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"
    assert {span.status_code for span in run.spans.all()} == {"OK", "ERROR"}


@pytest.mark.django_db
def test_evaluation_parse_failure_skips_score_without_crashing_worker():
    run = Run.objects.create(
        agent_name="test_agent",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
        total_tokens=100,
    )

    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="root",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={"input": "test query"},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="synthesis",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={
            "model": "gpt-4",
            "prompt_tokens": 50,
            "completion_tokens": 50,
            "output": "test answer",
        },
    )

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = "invalid json"

        evaluate_run.call_local(str(run.trace_id))

    assert not Evaluation.objects.filter(trace_id=run).exists()

    run.refresh_from_db()
    assert run.eval_score is None
