import json
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from huey.contrib.djhuey import db_task

from core.evaluation.prompts.v1 import get_judge_prompt_v1
from core.metrics import metrics
from core.models import Evaluation, Run
from core.providers.factory import create_llm_provider

logger = logging.getLogger("telemetry_lab")

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


@db_task()
def evaluate_run(trace_id):
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
    evaluation.status = "running"
    evaluation.prompt_version = prompt_version
    evaluation.error_message = None
    evaluation.started_at = started_at
    evaluation.completed_at = None
    evaluation.duration_ms = None
    evaluation.correctness_score = None
    evaluation.helpfulness_score = None
    evaluation.aggregate_score = None
    evaluation.reasoning = None
    evaluation.save(
        update_fields=[
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
    )

    spans = list(run.spans.order_by("start_time"))
    if not spans:
        completed_at = timezone.now()
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))

        evaluation.status = "failed"
        evaluation.error_message = "Run has no spans"
        evaluation.completed_at = completed_at
        evaluation.duration_ms = duration_ms
        evaluation.save(update_fields=["status", "error_message", "completed_at", "duration_ms"])

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
                    "failure_class": "ValueError",
                    "failure_message": "Run has no spans",
                },
            },
        )
        return

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

    try:
        prompt = get_judge_prompt_v1(user_query, final_answer, tool_summaries)

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

        completed_at = timezone.now()
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))

        evaluation.status = "completed"
        evaluation.correctness_score = parsed["correctness"]
        evaluation.helpfulness_score = parsed["helpfulness"]
        evaluation.aggregate_score = aggregate_score
        evaluation.reasoning = parsed["reasoning"]
        evaluation.error_message = None
        evaluation.completed_at = completed_at
        evaluation.duration_ms = duration_ms
        evaluation.save(
            update_fields=[
                "status",
                "correctness_score",
                "helpfulness_score",
                "aggregate_score",
                "reasoning",
                "error_message",
                "completed_at",
                "duration_ms",
            ]
        )

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
    except Exception as exc:
        completed_at = timezone.now()
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))

        failure_message = str(exc)
        if isinstance(exc, json.JSONDecodeError):
            failure_message = "Failed to parse judge response as JSON"

        evaluation.status = "failed"
        evaluation.error_message = failure_message
        evaluation.completed_at = completed_at
        evaluation.duration_ms = duration_ms
        evaluation.save(update_fields=["status", "error_message", "completed_at", "duration_ms"])

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
                    "failure_class": exc.__class__.__name__,
                    "failure_message": failure_message,
                },
            },
        )
