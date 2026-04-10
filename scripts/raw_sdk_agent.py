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
    raise RuntimeError("INGEST_API_KEY is required to run scripts/raw_sdk_agent.py")

tracer = Tracer(
    os.environ.get("TELEMETRY_BASE_URL", "http://127.0.0.1:8000"),
    ingest_api_key,
)
tracer.agent_name = "raw_sdk_briefing_agent"
atexit.register(tracer.shutdown)


def summarize_topic(user_query: str) -> str:
    words = [word.strip(".,?!") for word in user_query.lower().split() if word.strip(".,?!")]
    keywords = [word for word in words if len(word) > 3][:3]
    return ", ".join(keywords) if keywords else "agent telemetry"


def build_evidence(topic_summary: str) -> list[str]:
    return [
        "Shared tracer boundary keeps instrumentation outside the app framework.",
        "HTTP span export still hits /api/v1/ingest/span/ like the main demo.",
        f"This run focused on: {topic_summary}.",
    ]


def draft_brief(user_query: str, evidence: list[str]) -> str:
    bullets = " ".join(f"- {item}" for item in evidence)
    return (
        f"Brief for '{user_query}': {bullets} "
        "This agent is hand-rolled Python, but its spans land in the same backend pipeline."
    )


if __name__ == "__main__":
    user_query = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Show how a raw Python agent can share the telemetry tracer."
    )

    with tracer.span("raw_sdk_briefing_run", "chain") as root_span:
        root_span.set_attribute("input", user_query)
        root_span.set_attribute("agent_style", "raw_sdk")

        with tracer.span("topic_summary", "tool", parent_span_id=root_span.span_id) as topic_span:
            topic_summary = summarize_topic(user_query)
            topic_span.set_attribute("output", {"topic_summary": topic_summary})

        with tracer.span(
            "evidence_pack", "tool", parent_span_id=root_span.span_id
        ) as evidence_span:
            evidence = build_evidence(topic_summary)
            evidence_span.set_attribute("output", evidence)

        with tracer.span("template_reasoner", "llm", parent_span_id=root_span.span_id) as llm_span:
            final_brief = draft_brief(user_query, evidence)
            llm_span.set_attribute("model", "rule_based_template_v1")
            llm_span.set_attribute("prompt_tokens", len(user_query.split()) + len(evidence) * 8)
            llm_span.set_attribute("completion_tokens", len(final_brief.split()))
            llm_span.set_attribute("input", {"question": user_query, "evidence": evidence})
            llm_span.set_attribute("output", final_brief)

        tracer.finish({"output": final_brief, "agent_style": "raw_sdk"})
        logger.info(
            "Raw SDK agent completed",
            extra={
                "extra_fields": {
                    "output_length": len(final_brief),
                    "agent_style": "raw_sdk",
                }
            },
        )
