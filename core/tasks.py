import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, TypeVar, cast
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from huey.contrib.djhuey import db_task

from core.evaluation.prompts.v1 import get_judge_prompt_v1
from core.metrics import metrics
from core.models import Evaluation, Run, Span
from core.providers.factory import create_llm_provider

logger = logging.getLogger("telemetry_lab")

TaskFunc = TypeVar("TaskFunc", bound=Callable[..., Any])


def _typed_db_task() -> Callable[[TaskFunc], TaskFunc]:
    return cast(Callable[[TaskFunc], TaskFunc], db_task())


# --- PRODUCTION PATH NOTE ---
# The evaluate_run() task below uses Huey with a PostgreSQL backend.
# This is the correct choice for a single-developer portfolio scope.
#
# In a production async pipeline, this maps to:
#   1. The ingestion API publishes a message to an SQS queue on run completion.
#   2. A separate Celery worker (deployed as an ECS task) consumes the queue.
#   3. ECS task autoscaling is driven by SQS queue depth (CloudWatch metric).
#   4. The evaluate_run logic is identical — only the trigger and deployment change.
#
# The management command `eval_pending` below is the synchronous equivalent.
# ----------------------------


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


@dataclass
class _SpanContent:
    user_query: str
    final_answer: str
    tool_summaries: list[str]


def _extract_span_content(spans: list[Span]) -> _SpanContent:
    """Walk spans to extract the user query, final answer, and tool call summaries."""
    user_query = next(
        (s.attributes.get("input", "") for s in spans if s.span_type == "chain"),
        "",
    )
    final_answer = next(
        (s.attributes.get("output", "") for s in spans if s.span_type == "llm"),
        "",
    )
    tool_summaries = [
        f"{s.name}: {str(s.attributes.get('output', s.attributes.get('error_message', '')))[:200]}"
        for s in spans
        if s.span_type == "tool"
    ]
    return _SpanContent(
        user_query=user_query, final_answer=final_answer, tool_summaries=tool_summaries
    )


@_typed_db_task()
def evaluate_run(trace_id: UUID) -> None:
    prompt_version = "v1"
    provider_name = settings.EVAL_LLM_PROVIDER

    try:
        run = Run.objects.get(trace_id=trace_id)
    except Run.DoesNotExist:
        logger.warning("Run does not exist", extra={"trace_id": str(trace_id)})
        return

    started_at = timezone.now()
    metrics.increment_eval_tasks_started()
    logger.info(
        "Evaluation started",
        extra={
            "trace_id": str(trace_id),
            "extra_fields": {
                "outcome": "started",
                "prompt_version": prompt_version,
                "provider": provider_name,
            },
        },
    )

    evaluation, _ = Evaluation.objects.get_or_create(trace_id=run)
    _reset_evaluation(evaluation, prompt_version=prompt_version, started_at=started_at)

    spans = list(run.spans.order_by("start_time"))
    if not spans:
        _fail_evaluation(
            evaluation=evaluation,
            run=run,
            started_at=started_at,
            trace_id=trace_id,
            prompt_version=prompt_version,
            provider_name=provider_name,
            error_message="Run has no spans",
            failure_class="ValueError",
        )
        return

    content = _extract_span_content(spans)

    try:
        prompt = get_judge_prompt_v1(
            content.user_query, content.final_answer, content.tool_summaries
        )

        provider = create_llm_provider()
        response_text = provider.create_completion(
            messages=[
                {"role": "system", "content": "Respond only with a valid JSON object."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

        parsed = json.loads(response_text)
        aggregate_score = Decimal(str((parsed["correctness"] + parsed["helpfulness"]) / 2))

        _complete_evaluation(
            evaluation=evaluation,
            run=run,
            started_at=started_at,
            trace_id=trace_id,
            prompt_version=prompt_version,
            provider_name=provider_name,
            aggregate_score=aggregate_score,
            correctness=parsed["correctness"],
            helpfulness=parsed["helpfulness"],
            reasoning=parsed["reasoning"],
        )
    except Exception as exc:
        failure_message = str(exc)
        if isinstance(exc, json.JSONDecodeError):
            failure_message = "Failed to parse judge response as JSON"
        _fail_evaluation(
            evaluation=evaluation,
            run=run,
            started_at=started_at,
            trace_id=trace_id,
            prompt_version=prompt_version,
            provider_name=provider_name,
            error_message=failure_message,
            failure_class=exc.__class__.__name__,
        )


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

_EVALUATION_RESET_FIELDS = [
    "status",
    "prompt_version",
    "error_message",
    "started_at",
    "completed_at",
    "duration_ms",
    "correctness_score",
    "helpfulness_score",
    "aggregate_score",
    "reasoning",
]

_EVALUATION_COMPLETED_FIELDS = [
    "status",
    "correctness_score",
    "helpfulness_score",
    "aggregate_score",
    "reasoning",
    "error_message",
    "completed_at",
    "duration_ms",
]

_EVALUATION_FAILED_FIELDS = ["status", "error_message", "completed_at", "duration_ms"]


def _reset_evaluation(evaluation: Evaluation, *, prompt_version: str, started_at: datetime) -> None:
    evaluation.status = "running"
    evaluation.prompt_version = prompt_version
    evaluation.started_at = started_at
    evaluation.error_message = None
    evaluation.completed_at = None
    evaluation.duration_ms = None
    evaluation.correctness_score = None
    evaluation.helpfulness_score = None
    evaluation.aggregate_score = None
    evaluation.reasoning = None
    evaluation.save(update_fields=_EVALUATION_RESET_FIELDS)


def _complete_evaluation(
    *,
    evaluation: Evaluation,
    run: Run,
    started_at: datetime,
    trace_id: UUID,
    prompt_version: str,
    provider_name: str,
    aggregate_score: Decimal,
    correctness: int,
    helpfulness: int,
    reasoning: str,
) -> None:
    completed_at = timezone.now()
    duration_ms = _elapsed_ms(started_at, completed_at)

    evaluation.status = "completed"
    evaluation.correctness_score = correctness
    evaluation.helpfulness_score = helpfulness
    evaluation.aggregate_score = aggregate_score
    evaluation.reasoning = reasoning
    evaluation.error_message = None
    evaluation.completed_at = completed_at
    evaluation.duration_ms = duration_ms
    evaluation.save(update_fields=_EVALUATION_COMPLETED_FIELDS)

    run.eval_score = aggregate_score
    run.save(update_fields=["eval_score"])

    metrics.increment_eval_tasks_completed()
    logger.info(
        "Evaluation completed",
        extra={
            "trace_id": str(trace_id),
            "extra_fields": {
                "outcome": "completed",
                "prompt_version": prompt_version,
                "provider": provider_name,
                "duration_ms": duration_ms,
                "eval_score": str(aggregate_score),
            },
        },
    )


def _fail_evaluation(
    *,
    evaluation: Evaluation,
    run: Run,
    started_at: datetime,
    trace_id: UUID,
    prompt_version: str,
    provider_name: str,
    error_message: str,
    failure_class: str,
) -> None:
    completed_at = timezone.now()
    duration_ms = _elapsed_ms(started_at, completed_at)

    evaluation.status = "failed"
    evaluation.error_message = error_message
    evaluation.completed_at = completed_at
    evaluation.duration_ms = duration_ms
    evaluation.save(update_fields=_EVALUATION_FAILED_FIELDS)

    run.eval_score = None
    run.save(update_fields=["eval_score"])

    metrics.increment_eval_tasks_failed()
    logger.warning(
        "Evaluation failed",
        extra={
            "trace_id": str(trace_id),
            "extra_fields": {
                "outcome": "failed",
                "prompt_version": prompt_version,
                "provider": provider_name,
                "duration_ms": duration_ms,
                "failure_class": failure_class,
                "failure_message": error_message,
            },
        },
    )
