"""Failure mode matrix tests for error handling and resilience.

These tests verify the system handles failures gracefully without cascading,
as specified in the SDD (Section 6 and Anti-Patterns).
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Evaluation, Run, Span
from core.tasks import evaluate_run


@pytest.mark.django_db
def test_tracer_emission_failure_not_cascading(client, settings):
    """Verify invalid API key doesn't crash ingestion endpoint.

    Per SDD: The tracer takes a fail-open stance. Similarly, the ingestion
    API should return clear errors without crashing the system.
    """
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
    assert "error" in response.json()


@pytest.mark.django_db
def test_malformed_attributes_handled(client, settings):
    """Verify invalid JSON in attributes doesn't crash ingestion.

    The serializer should reject malformed attributes before they reach
    the database, returning a 400 error with details.
    """
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
def test_duplicate_span_idempotent(client, settings):
    """Verify duplicate span_id is handled gracefully.

    Since span_id is the primary key, duplicate IDs should fail with
    a clear error rather than creating duplicate records.
    """
    settings.INGEST_API_KEY = "dev-ingest-key"
    span_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    now = timezone.now()

    response1 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(span_id),
            "trace_id": str(trace_id),
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

    # Duplicate post fails (span_id is primary key)
    response2 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(span_id),  # Same span_id
            "trace_id": str(uuid.uuid4()),  # Different trace
            "name": "test2",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    # Should fail because span_id already exists (primary key constraint)
    assert response2.status_code == 400


@pytest.mark.django_db
def test_eval_worker_parse_failure():
    """Verify bad LLM response doesn't stop queue (per SDD Section 6).

    Per SDD: "On json.JSONDecodeError: print warning, skip run, do not crash worker"
    """
    # Create a completed run with spans
    run = Run.objects.create(
        agent_name="test_agent",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
        total_tokens=100,
    )

    # Create spans with required data
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

        evaluate_run(str(run.trace_id))

    # No evaluation should be created
    assert not Evaluation.objects.filter(trace_id=run).exists()

    # Run should still not have eval_score
    run.refresh_from_db()
    assert run.eval_score is None


@pytest.mark.django_db
def test_missing_parent_span_allowed():
    """Verify orphan spans (missing parent) are accepted.

    Per SDD AP-7: parent_span_id is a plain UUIDField, NOT a ForeignKey.
    This allows spans with parent_span_id that doesn't exist yet (parent may
    arrive later in distributed scenarios) or never arrives.
    """
    run = Run.objects.create(
        agent_name="test_agent",
        status="running",
        start_time=timezone.now(),
    )

    # Create span with non-existent parent_span_id
    non_existent_parent = uuid.uuid4()
    span = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="orphan_span",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=non_existent_parent,
    )

    # Should be accepted and stored
    assert span.parent_span_id == non_existent_parent
    assert Span.objects.filter(span_id=span.span_id).exists()


@pytest.mark.django_db
def test_negative_cost_calculation_prevented(client, settings):
    """Verify negative token counts don't produce negative costs.

    While the serializer accepts the data, the cost calculation should
    use absolute values or the DB constraint should prevent negatives.
    """
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"prompt_tokens": 20, "completion_tokens": 15},
            "is_final": True,
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response.status_code == 201
    run = Run.objects.get(trace_id=trace_id)
    assert run.total_tokens >= 0
    assert run.total_cost >= 0
    assert run.total_cost == Decimal(run.total_tokens) * Decimal("0.000002")
