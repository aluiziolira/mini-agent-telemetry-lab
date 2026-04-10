import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Run, Span
from core.views import build_span_tree


@pytest.mark.django_db
def test_first_span_creates_run(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(span_id),
            "trace_id": str(trace_id),
            "name": "test",
            "span_type": "chain",
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:01Z",
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response.status_code == 201
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "running"
    assert Span.objects.get(span_id=span_id).trace_id == run


@pytest.mark.django_db
def test_final_span_completes_run(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()

    first_response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:01Z",
            "status_code": "OK",
            "attributes": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert first_response.status_code == 201

    final_response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final",
            "span_type": "llm",
            "start_time": "2025-01-01T00:00:01Z",
            "end_time": "2025-01-01T00:00:02Z",
            "status_code": "OK",
            "attributes": {"prompt_tokens": 20, "completion_tokens": 15},
            "is_final": True,
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert final_response.status_code == 201
    assert final_response.json()["run_status"] == "completed"

    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"
    assert run.end_time is not None
    assert run.total_tokens == 50


@pytest.mark.django_db
def test_span_tree_nesting_depth():
    run = Run.objects.create(
        agent_name="agent", status="running", start_time=timezone.now()
    )
    root = Span(
        trace_id=run,
        span_type="chain",
        name="root",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
    )
    child = Span(
        trace_id=run,
        span_type="tool",
        name="child",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=root.span_id,
    )
    grandchild = Span(
        trace_id=run,
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
