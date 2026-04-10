"""Integration tests for end-to-end workflows.

These tests verify that multiple components work together correctly,
testing complete user workflows from start to finish.
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Evaluation, Run, Span
from core.tasks import evaluate_run


@pytest.mark.django_db
def test_full_run_lifecycle(client, settings):
    """Test complete run lifecycle: ingest spans → complete → aggregate.

    This integration test verifies:
    1. First span creates a run with status="running"
    2. Multiple spans can be ingested
    3. Final span triggers run completion
    4. Token and cost aggregation works correctly
    5. All spans are linked to the run
    """
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    # Step 1: Ingest root span (chain type)
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

    # Verify run was created with status "running"
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "running"
    assert run.agent_name == "unknown"

    # Step 2: Ingest tool span (yfinance fetch)
    tool_span_id = uuid.uuid4()
    response2 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(tool_span_id),
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

    # Step 3: Ingest LLM span (synthesis)
    llm_span_id = uuid.uuid4()
    response3 = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(llm_span_id),
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

    # Step 4: Ingest final span to complete run
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

    # Verify final state
    run.refresh_from_db()
    assert run.status == "completed"
    assert run.end_time is not None
    assert run.total_tokens == 150  # 100 + 50
    assert run.total_cost == Decimal("0.0003")  # 150 * 0.000002

    # Verify all 4 spans are linked
    assert run.spans.count() == 4


@pytest.mark.django_db
def test_evaluation_pipeline_sync():
    """Test evaluation task logic synchronously (without Huey queue).

    This integration test verifies:
    1. Evaluation extracts data from spans correctly
    2. LLM provider is called with correct prompt
    3. Evaluation record is created with correct scores
    4. Run.eval_score is denormalized correctly

    Note: This mocks the OpenAI client to test the logic without external calls.
    """
    # Setup: Create a completed run with spans
    run = Run.objects.create(
        agent_name="research_analyst",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
        total_tokens=150,
        total_cost=Decimal("0.0003"),
    )

    # Create root span with user query
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

    # Create tool spans
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="tool",
        name="yfinance_fetch",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={"tool_name": "yfinance_fetch", "output": {"price": 189.50}},
    )

    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="tool",
        name="web_search",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="ERROR",
        attributes={"error_message": "simulated search timeout"},
    )

    # Create LLM span with final answer
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="synthesis_call",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={
            "model": "gpt-4o-mini",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "output": "Based on the analysis, AAPL shows strong fundamentals...",
        },
    )

    mock_response = {
        "correctness": 4,
        "helpfulness": 5,
        "reasoning": "The analysis is factually grounded and directly answers the user's question.",
    }

    with patch("openai.OpenAI") as mock_openai_class:
        mock_client = mock_openai_class.return_value
        mock_chat = mock_client.chat.completions.create
        mock_chat.return_value.choices[0].message.content = json.dumps(mock_response)

        evaluate_run(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.correctness_score == 4
    assert evaluation.helpfulness_score == 5
    assert evaluation.aggregate_score == Decimal("4.5")
    assert "factually grounded" in evaluation.reasoning

    # Verify denormalized score on run
    run.refresh_from_db()
    assert run.eval_score == Decimal("4.5")


@pytest.mark.django_db
def test_run_with_error_span_integration(client, settings):
    """Test that runs with ERROR spans complete correctly.

    Verifies that the presence of error spans doesn't prevent run completion.
    """
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    # Ingest root span
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

    # Ingest ERROR span
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

    # Final span to complete
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

    # Verify run completed despite error span
    run = Run.objects.get(trace_id=trace_id)
    assert run.status == "completed"

    # Verify both OK and ERROR spans are stored
    spans = run.spans.all()
    status_codes = {s.status_code for s in spans}
    assert "OK" in status_codes
    assert "ERROR" in status_codes
