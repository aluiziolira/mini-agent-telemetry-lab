from datetime import datetime
from decimal import Decimal
from typing import Any

from core.models import Run, Span
from core.validators import ValidationError

# $2.00 per 1M tokens (GPT-4o-mini pricing).
# In production, this would be provider-specific and configurable via settings.
_COST_PER_TOKEN = Decimal("0.000002")


def _coerce_token_count(value: Any, *, field_name: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_name} must be an integer")
    if value < 0:
        raise ValidationError(f"{field_name} must be non-negative")
    return int(value)


def span_token_total(attributes: dict[str, Any]) -> int:
    prompt_tokens = _coerce_token_count(
        attributes.get("prompt_tokens", 0),
        field_name="prompt_tokens",
    )
    completion_tokens = _coerce_token_count(
        attributes.get("completion_tokens", 0),
        field_name="completion_tokens",
    )
    return prompt_tokens + completion_tokens


def finalize_run_if_needed(*, run: Run, is_final: bool, end_time: datetime) -> bool:
    if not is_final:
        return False

    if run.status == "completed":
        return True

    spans = Span.objects.filter(trace_id=run)
    total_tokens = sum(span_token_total(span.attributes) for span in spans)
    total_cost = Decimal(total_tokens) * _COST_PER_TOKEN

    run.status = "completed"
    run.end_time = end_time
    run.total_tokens = total_tokens
    run.total_cost = total_cost
    run.save(update_fields=["status", "end_time", "total_tokens", "total_cost"])
    return True
