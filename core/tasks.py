import json
import logging
from decimal import Decimal

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
    try:
        run = Run.objects.get(trace_id=trace_id)
    except Run.DoesNotExist:
        logger.warning("Run does not exist", extra={"trace_id": str(trace_id)})
        return

    spans = list(run.spans.order_by("start_time"))
    if not spans:
        logger.warning("Run has no spans", extra={"trace_id": str(trace_id)})
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

    prompt = get_judge_prompt_v1(user_query, final_answer, tool_summaries)

    provider = create_llm_provider()
    response_text = provider.create_completion(
        messages=[
            {"role": "system", "content": "Respond only with a valid JSON object."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse judge response", extra={"trace_id": str(trace_id)})
        return

    aggregate_score = Decimal(str((parsed["correctness"] + parsed["helpfulness"]) / 2))
    Evaluation.objects.create(
        trace_id=run,
        correctness_score=parsed["correctness"],
        helpfulness_score=parsed["helpfulness"],
        aggregate_score=aggregate_score,
        reasoning=parsed["reasoning"],
        prompt_version="v1",
    )
    run.eval_score = aggregate_score
    run.save()

    metrics.increment_eval_tasks_completed()
    logger.info(
        "Evaluation completed",
        extra={
            "trace_id": str(trace_id),
            "extra_fields": {"eval_score": str(aggregate_score)},
        },
    )
