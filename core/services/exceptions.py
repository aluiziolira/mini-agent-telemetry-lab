class IngestionError(Exception):
    """Base error for ingestion services."""


class IdempotentDuplicateError(IngestionError):
    """Raised when an idempotency key has already been claimed with the same payload."""

    def __init__(self, message: str, *, span_id: str, run_completed: bool) -> None:
        super().__init__(message)
        self.span_id = span_id
        self.run_completed = run_completed


class IdempotencyConflictError(IngestionError):
    """Raised when an idempotency key is reused with a different payload."""


class ParentSpanNotFoundError(IngestionError):
    """Raised when parent span is not found in trace."""


class CompletedRunConflictError(IngestionError):
    """Raised when writing a new span to a completed run."""


class SpanAlreadyExistsError(IngestionError):
    """Raised when span_id uniqueness is violated."""
