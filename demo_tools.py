from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def run_tool_with_retries(
    tracer,
    *,
    tool_name: str,
    parent_span_id: str,
    operation: Callable,
    max_attempts: int = 2,
):
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            with tracer.span(
                tool_name,
                "tool",
                parent_span_id=parent_span_id,
                attrs={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
            ) as tool_span:
                result = operation(tool_span)
                tool_span.set_attribute("retry_count", attempt - 1)
                return result
        except Exception as exc:
            last_error = exc
            if attempt == max_attempts:
                raise

    raise last_error
