from datetime import datetime
from decimal import Decimal

from core.models import Run, Span

# $2.00 per 1M tokens (GPT-4o-mini pricing).
# In production, this would be provider-specific and configurable via settings.
_COST_PER_TOKEN = Decimal("0.000002")


def finalize_run_if_needed(*, run: Run, is_final: bool, end_time: datetime) -> bool:
    if not is_final:
        return False

    if run.status == "completed":
        return True

    spans = Span.objects.filter(trace_id=run)
    total_tokens = sum(
        span.attributes.get("prompt_tokens", 0) + span.attributes.get("completion_tokens", 0)
        for span in spans
    )
    total_cost = Decimal(total_tokens) * _COST_PER_TOKEN

    run.status = "completed"
    run.end_time = end_time
    run.total_tokens = total_tokens
    run.total_cost = total_cost
    run.save(update_fields=["status", "end_time", "total_tokens", "total_cost"])
    return True
