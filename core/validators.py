"""Span attribute validators for data integrity.

These validators ensure span attributes conform to the expected schema
for each span_type, enforcing data quality at the ingestion boundary.
"""

from core.types import SpanAttributes, SpanType


class ValidationError(ValueError):
    """Raised when span attributes fail validation."""

    pass


def validate_span_attributes(span_type: SpanType, attributes: SpanAttributes) -> bool:
    """Validate span attributes based on span_type schema.

    Args:
        span_type: The type of span (llm, tool, chain)
        attributes: Dict of span attributes

    Raises:
        ValidationError: If attributes don't match the expected schema
    """
    if span_type == "llm":
        required = ["model", "prompt_tokens", "completion_tokens"]
        for field in required:
            if field not in attributes:
                raise ValidationError(f"LLM span missing required attribute: {field}")
            if field in ["prompt_tokens", "completion_tokens"]:
                if not isinstance(attributes[field], int):
                    raise ValidationError(f"LLM span {field} must be an integer")
                if attributes[field] < 0:
                    raise ValidationError(f"LLM span {field} must be non-negative")

    elif span_type == "tool":
        if "tool_name" not in attributes and "error_message" not in attributes:
            raise ValidationError("Tool span must have tool_name or error_message")

    elif span_type == "chain":
        pass

    return True
