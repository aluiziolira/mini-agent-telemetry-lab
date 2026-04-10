class IngestionError(Exception):
    """Base error for ingestion services."""


class IdempotentDuplicateError(IngestionError):
    """Raised when an idempotency key has already been claimed."""


class ParentSpanNotFoundError(IngestionError):
    """Raised when parent span is not found in trace."""


class CompletedRunConflictError(IngestionError):
    """Raised when writing a new span to a completed run."""


class SpanAlreadyExistsError(IngestionError):
    """Raised when span_id uniqueness is violated."""
