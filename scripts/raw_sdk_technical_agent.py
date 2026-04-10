import atexit
import logging
import logging.config
import os
import sys

from dotenv import load_dotenv

from sdk.tracer import Tracer
from telemetry_lab.logging_config import get_logging_config

load_dotenv()
logging.config.dictConfig(get_logging_config())
logger = logging.getLogger("telemetry_lab")

ingest_api_key = os.environ.get("INGEST_API_KEY")
if not ingest_api_key:
    raise RuntimeError("INGEST_API_KEY is required to run scripts/raw_sdk_technical_agent.py")

tracer = Tracer(
    os.environ.get("TELEMETRY_BASE_URL", "http://127.0.0.1:8000"),
    ingest_api_key,
)
tracer.agent_name = "raw_sdk_technical_explainer_agent"
atexit.register(tracer.shutdown)


def summarize_question_focus(user_query: str) -> str:
    tokens = [token.strip(".,?!") for token in user_query.lower().split() if token.strip(".,?!")]
    technical_terms = [token for token in tokens if len(token) > 4][:3]
    return ", ".join(technical_terms) if technical_terms else "event loop fundamentals"


def build_explanation_points(question_focus: str) -> list[str]:
    return [
        "Start from first principles, then connect to runtime behavior.",
        "Contrast high-level intuition with concrete execution details.",
        f"Anchor the answer on: {question_focus}.",
    ]


def draft_explanation(user_query: str, explanation_points: list[str]) -> str:
    bullets = " ".join(f"- {item}" for item in explanation_points)
    return (
        f"Technical explanation for '{user_query}': {bullets} "
        "This raw Python demo mirrors the same tracer flow while answering an engineering question."
    )


if __name__ == "__main__":
    user_query = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "How does Python's event loop schedule coroutines during high I/O workloads?"
    )

    with tracer.span("raw_sdk_technical_run", "chain") as root_span:
        root_span.set_attribute("input", user_query)
        root_span.set_attribute("agent_style", "raw_sdk")

        with tracer.span("question_focus", "tool", parent_span_id=root_span.span_id) as focus_span:
            question_focus = summarize_question_focus(user_query)
            focus_span.set_attribute("output", {"question_focus": question_focus})

        with tracer.span(
            "explanation_points", "tool", parent_span_id=root_span.span_id
        ) as points_span:
            explanation_points = build_explanation_points(question_focus)
            points_span.set_attribute("output", explanation_points)

        with tracer.span("template_reasoner", "llm", parent_span_id=root_span.span_id) as llm_span:
            final_explanation = draft_explanation(user_query, explanation_points)
            llm_span.set_attribute("model", "rule_based_template_v1")
            llm_span.set_attribute(
                "prompt_tokens", len(user_query.split()) + len(explanation_points) * 8
            )
            llm_span.set_attribute("completion_tokens", len(final_explanation.split()))
            llm_span.set_attribute(
                "input", {"question": user_query, "explanation_points": explanation_points}
            )
            llm_span.set_attribute("output", final_explanation)

        tracer.finish({"output": final_explanation, "agent_style": "raw_sdk"})
        logger.info(
            "Raw SDK technical agent completed",
            extra={
                "extra_fields": {
                    "output_length": len(final_explanation),
                    "agent_style": "raw_sdk",
                }
            },
        )
