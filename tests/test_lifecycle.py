"""High-signal lifecycle tests for ingestion and trace reconstruction."""

import uuid
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Run, Span
from core.views import build_span_tree


@pytest.mark.django_db
def test_full_run_lifecycle_rolls_up_metrics_and_links_spans(client, settings):
    """A completed run should preserve the end-to-end telemetry story."""
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    root_span_id = uuid.uuid4()
    response1 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(root_span_id),
            "trace_id": str(trace_id),
            "name": "research_analyst_run",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"input": "Should I buy AAPL?"},
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response1.status_code == 201

    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "running"

    response2 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "parent_span_id": str(root_span_id),
            "name": "yfinance_fetch",
            "span_type": "tool",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {
                "tool_name": "yfinance_fetch",
                "output": {"symbol": "AAPL", "price": 189.50},
            },
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
            "parent_span_id": str(root_span_id),
            "name": "synthesis_call",
            "span_type": "llm",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {
                "model": "gpt-4o-mini",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "input": "User query and stock data...",
                "output": "Based on the analysis...",
            },
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response3.status_code == 201

    response4 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "parent_span_id": str(root_span_id),
            "name": "completion",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "attributes": {"output": "Final recommendation"},
            "is_final": True,
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert response4.status_code == 201
    assert response4.json()["run_status"] == "completed"

    run.refresh_from_db()
    assert run.status == "completed"
    assert run.end_time is not None
    assert run.agent_name == "unknown"
    assert run.total_tokens == 150
    assert run.total_cost == Decimal("0.0003")
    assert run.spans.count() == 4


@pytest.mark.django_db
def test_build_span_tree_reconstructs_nested_trace_for_run_detail():
    """Nested spans should rebuild into the same trace shape shown to reviewers."""
    run = Run.objects.create(
        agent_name="agent",
        status="running",
        start_time=timezone.now(),
    )
    root = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="root",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
    )
    child = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="tool",
        name="child",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=root.span_id,
    )
    grandchild = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="grandchild",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=child.span_id,
    )

    tree = build_span_tree([root, child, grandchild])

    assert len(tree) == 1
    assert tree[0]["span"].name == "root"
    assert tree[0]["children"][0]["span"].name == "child"
    assert tree[0]["children"][0]["children"][0]["span"].name == "grandchild"
