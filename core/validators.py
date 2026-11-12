"""Span attribute validators for data integrity.

These validators ensure span attributes conform to the expected schema
for each span_type, enforcing data quality at the ingestion boundary.
"""

from core.types import SpanAttributes, SpanType

_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens")


class ValidationError(ValueError):
    """Raised when span attributes fail validation."""

    pass


def validate_token_fields(attributes: SpanAttributes, *, span_label: str) -> None:
    """Reject non-integer or negative token fields when present on any span type."""
    for field in _TOKEN_FIELDS:
        if field not in attributes:
            continue
        value = attributes[field]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"{span_label} {field} must be an integer")
        if value < 0:
            raise ValidationError(f"{span_label} {field} must be non-negative")


def validate_span_attributes(span_type: SpanType, attributes: SpanAttributes) -> bool:
    """Validate span attributes based on span_type schema.

    Args:
        span_type: The type of span (llm, tool, chain)
        attributes: Dict of span attributes

    Raises:
        ValidationError: If attributes don't match the expected schema
    """
    if not isinstance(attributes, dict):
        raise ValidationError("attributes must be an object")

    if span_type == "llm":
        required = ["model", "prompt_tokens", "completion_tokens"]
        for field in required:
            if field not in attributes:
                raise ValidationError(f"LLM span missing required attribute: {field}")
        validate_token_fields(attributes, span_label="LLM span")

    elif span_type == "tool":
        if "tool_name" not in attributes and "error_message" not in attributes:
            raise ValidationError("Tool span must have tool_name or error_message")
        validate_token_fields(attributes, span_label="Tool span")

    elif span_type == "chain":
        validate_token_fields(attributes, span_label="Chain span")

    else:
        raise ValidationError(f"Unsupported span_type: {span_type}")

    return True
