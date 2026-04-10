import os
import json
from decimal import Decimal

from huey.contrib.djhuey import db_task
from openai import OpenAI

from core.models import Evaluation, Run

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
        print(f"[eval] warn: run {trace_id} does not exist")
        return

    spans = list(run.spans.order_by("start_time"))
    if not spans:
        print(f"[eval] warn: run {trace_id} has no spans")
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

    prompt = (
        "You are grading an AI investment-assistant trace. "
        "Score correctness (1-5) based on factual grounding and helpfulness (1-5) based on whether it answers the user's question directly. "
        "Return a JSON object with exactly these keys: correctness, helpfulness, reasoning.\n\n"
        f"User question: {user_query}\n"
        f"Final answer: {final_answer}\n"
        f"Tool summaries: {tool_summaries}\n"
    )

    client = OpenAI(api_key=os.environ["LLM_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Respond only with a valid JSON object.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    response_text = response.choices[0].message.content

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        print(f"[eval] warn: failed to parse judge response for {trace_id}")
        return

    aggregate_score = Decimal(str((parsed["correctness"] + parsed["helpfulness"]) / 2))
    Evaluation.objects.create(
        trace_id=run,
        correctness_score=parsed["correctness"],
        helpfulness_score=parsed["helpfulness"],
        aggregate_score=aggregate_score,
        reasoning=parsed["reasoning"],
    )
    run.eval_score = aggregate_score
    run.save()
