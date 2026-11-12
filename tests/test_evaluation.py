"""Integration-style tests for the evaluator credibility loop."""

import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Evaluation, Run, Span
from core.tasks import evaluate_run


def _create_completed_run_for_evaluation():
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

    return run


@pytest.mark.django_db
def test_completed_run_is_scored_and_denormalized_for_review():
    """A finished run should produce an explainable evaluation artifact."""
    run = _create_completed_run_for_evaluation()

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
    assert evaluation.status == "completed"
    assert evaluation.error_message is None
    assert evaluation.started_at is not None
    assert evaluation.completed_at is not None
    assert evaluation.duration_ms >= 0

    run.refresh_from_db()
    assert run.eval_score == Decimal("4.5")


@pytest.mark.django_db
def test_parse_failure_is_persisted_as_durable_failed_evidence():
    run = _create_completed_run_for_evaluation()

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = "invalid json"

        evaluate_run.call_local(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.status == "failed"
    assert evaluation.correctness_score is None
    assert evaluation.helpfulness_score is None
    assert evaluation.aggregate_score is None
    assert evaluation.reasoning in (None, "")
    assert "parse" in evaluation.error_message.lower()
    assert evaluation.started_at is not None
    assert evaluation.completed_at is not None
    assert evaluation.duration_ms >= 0

    run.refresh_from_db()
    assert run.eval_score is None


@pytest.mark.django_db
def test_rerun_updates_existing_evaluation_record_instead_of_duplicating_it():
    run = _create_completed_run_for_evaluation()

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = "invalid json"
        evaluate_run.call_local(str(run.trace_id))

        first_evaluation = Evaluation.objects.get(trace_id=run)
        first_evaluation_id = first_evaluation.id
        assert first_evaluation.status == "failed"

        mock_provider.create_completion.return_value = json.dumps(
            {
                "correctness": 4,
                "helpfulness": 5,
                "reasoning": "Second attempt succeeded with grounded analysis.",
            }
        )
        evaluate_run.call_local(str(run.trace_id))

    assert Evaluation.objects.filter(trace_id=run).count() == 1
    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.id == first_evaluation_id
    assert evaluation.status == "completed"
    assert evaluation.error_message in (None, "")
    assert evaluation.aggregate_score == Decimal("4.5")

    run.refresh_from_db()
    assert run.eval_score == Decimal("4.5")


@pytest.mark.django_db
def test_provider_exception_is_persisted_as_durable_failed_evidence():
    run = _create_completed_run_for_evaluation()

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.side_effect = RuntimeError("provider timeout")

        evaluate_run.call_local(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.status == "failed"
    assert evaluation.correctness_score is None
    assert evaluation.helpfulness_score is None
    assert evaluation.aggregate_score is None
    assert "provider timeout" in evaluation.error_message.lower()
    assert evaluation.started_at is not None
    assert evaluation.completed_at is not None
    assert evaluation.duration_ms >= 0

    run.refresh_from_db()
    assert run.eval_score is None


@pytest.mark.django_db
def test_preparation_failure_outside_provider_persists_failed_status():
    run = _create_completed_run_for_evaluation()

    with patch(
        "core.tasks._extract_span_content",
        side_effect=RuntimeError("span content extraction failed"),
    ):
        evaluate_run.call_local(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.status == "failed"
    assert "span content extraction failed" in evaluation.error_message
    assert evaluation.started_at is not None
    assert evaluation.completed_at is not None
    assert evaluation.duration_ms >= 0

    run.refresh_from_db()
    assert run.eval_score is None


@pytest.mark.django_db
def test_empty_run_preparation_failure_persists_failed_status():
    run = Run.objects.create(
        agent_name="empty_agent",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
    )

    evaluate_run.call_local(str(run.trace_id))

    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.status == "failed"
    assert evaluation.error_message == "Run has no spans"


@pytest.mark.django_db
def test_evaluation_uses_final_llm_output_not_intermediate_draft():
    from datetime import timedelta

    run = Run.objects.create(
        agent_name="research_analyst",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
        total_tokens=150,
        total_cost=Decimal("0.0003"),
    )
    t0 = timezone.now()
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="research_analyst_run",
        start_time=t0,
        end_time=t0,
        status_code="OK",
        attributes={"input": "Should I buy AAPL?"},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="draft_call",
        start_time=t0,
        end_time=t0 + timedelta(seconds=1),
        status_code="OK",
        attributes={
            "model": "gpt-4o-mini",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "output": "INTERMEDIATE_DRAFT",
        },
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="synthesis_call",
        start_time=t0 + timedelta(seconds=2),
        end_time=t0 + timedelta(seconds=3),
        status_code="OK",
        attributes={
            "model": "gpt-4o-mini",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "output": "FINAL_ANSWER_TEXT",
        },
    )

    captured: dict[str, str] = {}

    def fake_prompt(user_query: str, final_answer: str, tool_summaries: list[str]) -> str:
        captured["final_answer"] = final_answer
        return "prompt"

    with (
        patch("core.tasks.get_judge_prompt_v1", side_effect=fake_prompt),
        patch("core.tasks.create_llm_provider") as mock_factory,
    ):
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = json.dumps(
            {
                "correctness": 5,
                "helpfulness": 5,
                "reasoning": "Used the final synthesis output.",
            }
        )
        evaluate_run.call_local(str(run.trace_id))

    assert captured["final_answer"] == "FINAL_ANSWER_TEXT"
    evaluation = Evaluation.objects.get(trace_id=run)
    assert evaluation.status == "completed"
