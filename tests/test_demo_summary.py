import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Evaluation, Run, Span


@pytest.mark.django_db
def test_demo_summary_surfaces_live_run_metrics_and_step_breakdown():
    now = timezone.now()

    run = Run.objects.create(
        agent_name="research_analyst",
        status="completed",
        start_time=now,
        end_time=now + timedelta(milliseconds=420),
        total_tokens=150,
        total_cost=Decimal("0.0003"),
        eval_score=Decimal("4.5"),
    )
    Evaluation.objects.create(
        trace_id=run,
        correctness_score=4,
        helpfulness_score=5,
        aggregate_score=Decimal("4.5"),
        reasoning="Factually grounded and useful.",
        prompt_version="v1",
    )

    root_span_id = uuid.uuid4()
    Span.objects.create(
        trace_id=run,
        span_id=root_span_id,
        span_type="chain",
        name="research_analyst_run",
        start_time=now,
        end_time=now + timedelta(milliseconds=50),
        status_code="OK",
        attributes={"input": "Should I buy AAPL?"},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        parent_span_id=root_span_id,
        span_type="tool",
        name="yfinance_fetch",
        start_time=now + timedelta(milliseconds=50),
        end_time=now + timedelta(milliseconds=170),
        status_code="OK",
        attributes={"output": {"symbol": "AAPL", "price": 189.50}},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        parent_span_id=root_span_id,
        span_type="llm",
        name="synthesis_call",
        start_time=now + timedelta(milliseconds=170),
        end_time=now + timedelta(milliseconds=370),
        status_code="OK",
        attributes={"prompt_tokens": 100, "completion_tokens": 50},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        parent_span_id=root_span_id,
        span_type="chain",
        name="completion",
        start_time=now + timedelta(milliseconds=370),
        end_time=now + timedelta(milliseconds=420),
        status_code="OK",
        attributes={"output": "Final recommendation"},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="run_finish",
        start_time=now + timedelta(milliseconds=420),
        end_time=now + timedelta(milliseconds=420),
        status_code="OK",
        attributes={"output": "Final recommendation"},
    )

    out = StringIO()
    call_command("demo_summary", "--limit", "1", stdout=out)
    output = out.getvalue()

    assert "LIVE DEMO EXECUTION SUMMARY" in output
    assert "Run 1: research_analyst" in output
    assert (
        "status=completed | spans=5 | latency=420.0ms | tokens=150 | cost=$0.0003 | eval_score=4.5"
        in output
    )
    assert "1. research_analyst_run [chain OK] duration=50.0ms" in output
    assert "2. yfinance_fetch [tool OK] duration=120.0ms" in output
    assert "3. synthesis_call [llm OK] duration=200.0ms | tokens=150" in output
    assert "4. completion [chain OK] duration=50.0ms" in output
    assert "5. run_finish [chain OK] duration=synthetic | completion_marker=true" in output


@pytest.mark.django_db
def test_demo_summary_shows_error_attempts_and_sub_millisecond_durations():
    now = timezone.now()
    run = Run.objects.create(
        agent_name="research_analyst",
        status="completed",
        start_time=now,
        end_time=now + timedelta(milliseconds=10),
        total_tokens=91,
        total_cost=Decimal("0.0002"),
        eval_score=Decimal("3.5"),
    )

    root_span_id = uuid.uuid4()
    Span.objects.create(
        trace_id=run,
        span_id=root_span_id,
        span_type="chain",
        name="research_analyst_run",
        start_time=now,
        end_time=now + timedelta(milliseconds=5),
        status_code="OK",
        attributes={},
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        parent_span_id=root_span_id,
        span_type="tool",
        name="web_search",
        start_time=now + timedelta(milliseconds=5),
        end_time=now + timedelta(milliseconds=5, microseconds=20),
        status_code="ERROR",
        attributes={
            "attempt": 1,
            "max_attempts": 2,
            "error_message": "simulated search timeout",
        },
    )
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        parent_span_id=root_span_id,
        span_type="tool",
        name="web_search",
        start_time=now + timedelta(milliseconds=6),
        end_time=now + timedelta(milliseconds=6, microseconds=30),
        status_code="OK",
        attributes={
            "attempt": 2,
            "max_attempts": 2,
            "retry_count": 1,
            "output": "Recovered search results",
        },
    )

    out = StringIO()
    call_command("demo_summary", "--limit", "1", stdout=out)
    output = out.getvalue()

    assert (
        "web_search [tool ERROR] duration=<0.1ms | attempt=1/2 | error=simulated search timeout"
        in output
    )
    assert "web_search [tool OK] duration=<0.1ms | attempt=2/2" in output
