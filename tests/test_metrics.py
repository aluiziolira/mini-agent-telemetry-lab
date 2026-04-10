import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.metrics import metrics
from core.models import MetricCounter, Run, Span
from core.tasks import evaluate_run


@pytest.mark.django_db
def test_metrics_endpoint_persists_span_ingestion_count(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    root_span_id = uuid.uuid4()
    responses = [
        client.post(
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
        ),
        client.post(
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
        ),
        client.post(
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
                    "output": "Based on the analysis...",
                },
            },
            content_type="application/json",
            headers={"X-API-Key": "dev-ingest-key"},
        ),
        client.post(
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
        ),
    ]

    for response in responses:
        assert response.status_code == 201

    counter = MetricCounter.objects.get(name="spans_ingested_total")
    assert counter.value == 4

    metrics_response = client.get(reverse("metrics"))

    assert metrics_response.status_code == 200
    assert metrics_response["Content-Type"] == "text/plain; charset=utf-8"
    assert "spans_ingested_total 4" in metrics_response.content.decode()
    assert "eval_tasks_completed_total 0" in metrics_response.content.decode()


@pytest.mark.django_db
def test_metrics_endpoint_reflects_completed_evaluations(client):
    run = Run.objects.create(
        agent_name="research_analyst",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
        total_tokens=150,
        total_cost=Decimal("0.0003"),
    )

    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="research_analyst_run",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={"input": "Should I buy AAPL?"},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="synthesis_call",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "output": "Based on the analysis, AAPL shows strong fundamentals...",
        },
    )

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = json.dumps(
            {
                "correctness": 4,
                "helpfulness": 5,
                "reasoning": "The analysis is factually grounded and directly answers the user's question.",
            }
        )

        evaluate_run.call_local(str(run.trace_id))

    assert MetricCounter.objects.get(name="eval_tasks_completed_total").value == 1
    assert MetricCounter.objects.get(name="spans_ingested_total").value == 0

    metrics_text = metrics.get_prometheus_text()

    assert "spans_ingested_total 0" in metrics_text
    assert "eval_tasks_completed_total 1" in metrics_text

    metrics_response = client.get(reverse("metrics"))
    assert metrics_response.status_code == 200
    assert "eval_tasks_completed_total 1" in metrics_response.content.decode()
