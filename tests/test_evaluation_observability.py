import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import MetricCounter, Run, Span
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
def test_evaluation_lifecycle_metrics_track_started_completed_and_failed_counts():
    success_run = _create_completed_run_for_evaluation()
    failed_run = _create_completed_run_for_evaluation()

    with patch("core.tasks.create_llm_provider") as mock_factory:
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.side_effect = [
            json.dumps(
                {
                    "correctness": 4,
                    "helpfulness": 5,
                    "reasoning": "Strongly grounded answer.",
                }
            ),
            RuntimeError("provider unavailable"),
        ]

        evaluate_run.call_local(str(success_run.trace_id))
        evaluate_run.call_local(str(failed_run.trace_id))

    assert MetricCounter.objects.get(name="eval_tasks_started_total").value == 2
    assert MetricCounter.objects.get(name="eval_tasks_completed_total").value == 1
    assert MetricCounter.objects.get(name="eval_tasks_failed_total").value == 1


@pytest.mark.django_db
def test_structured_logs_include_lifecycle_fields_for_success():
    run = _create_completed_run_for_evaluation()

    with (
        patch("core.tasks.create_llm_provider") as mock_factory,
        patch("core.tasks.logger") as mock_logger,
    ):
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.return_value = json.dumps(
            {
                "correctness": 4,
                "helpfulness": 5,
                "reasoning": "Strongly grounded answer.",
            }
        )

        evaluate_run.call_local(str(run.trace_id))

    start_call = next(
        call_args
        for call_args in mock_logger.info.call_args_list
        if call_args.args[0] == "Evaluation started"
    )
    completed_call = next(
        call_args
        for call_args in mock_logger.info.call_args_list
        if call_args.args[0] == "Evaluation completed"
    )

    start_extra = start_call.kwargs["extra"]
    completed_extra = completed_call.kwargs["extra"]

    assert start_extra["trace_id"] == str(run.trace_id)
    assert completed_extra["trace_id"] == str(run.trace_id)
    assert start_extra["extra_fields"]["prompt_version"] == "v1"
    assert completed_extra["extra_fields"]["prompt_version"] == "v1"
    assert start_extra["extra_fields"]["outcome"] == "started"
    assert completed_extra["extra_fields"]["outcome"] == "completed"
    assert completed_extra["extra_fields"]["duration_ms"] >= 0
    assert completed_extra["extra_fields"]["provider"] == "openai"
    assert completed_extra["extra_fields"]["eval_score"] == str(Decimal("4.5"))


@pytest.mark.django_db
def test_structured_logs_include_lifecycle_fields_for_failure():
    run = _create_completed_run_for_evaluation()

    with (
        patch("core.tasks.create_llm_provider") as mock_factory,
        patch("core.tasks.logger") as mock_logger,
    ):
        mock_provider = mock_factory.return_value
        mock_provider.create_completion.side_effect = RuntimeError("provider down")

        evaluate_run.call_local(str(run.trace_id))

    started_call = next(
        call_args
        for call_args in mock_logger.info.call_args_list
        if call_args.args[0] == "Evaluation started"
    )
    failed_call = next(
        call_args
        for call_args in mock_logger.warning.call_args_list
        if call_args.args[0] == "Evaluation failed"
    )

    started_extra = started_call.kwargs["extra"]
    failed_extra = failed_call.kwargs["extra"]

    assert started_extra["trace_id"] == str(run.trace_id)
    assert failed_extra["trace_id"] == str(run.trace_id)
    assert failed_extra["extra_fields"]["prompt_version"] == "v1"
    assert failed_extra["extra_fields"]["outcome"] == "failed"
    assert failed_extra["extra_fields"]["duration_ms"] >= 0
    assert failed_extra["extra_fields"]["provider"] == "openai"
    assert failed_extra["extra_fields"]["failure_class"] == "RuntimeError"
    assert failed_extra["extra_fields"]["failure_message"] == "provider down"
