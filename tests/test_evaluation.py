"""Integration-style tests for the evaluator credibility loop."""

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Evaluation, Run, Span
from core.tasks import evaluate_run


@pytest.mark.django_db
def test_completed_run_is_scored_and_denormalized_for_review():
    """A finished run should produce an explainable evaluation artifact."""
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

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = json.dumps(mock_response)

        evaluate_run.call_local(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.correctness_score == 4
    assert evaluation.helpfulness_score == 5
    assert evaluation.aggregate_score == Decimal("4.5")
    assert "factually grounded" in evaluation.reasoning

    run.refresh_from_db()
    assert run.eval_score == Decimal("4.5")
