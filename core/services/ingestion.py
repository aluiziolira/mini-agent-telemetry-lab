import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.metrics import metrics
from core.models import IdempotencyKey, Run, Span
from core.services.exceptions import (
    CompletedRunConflictError,
    IdempotencyConflictError,
    IdempotentDuplicateError,
    ParentSpanNotFoundError,
    SpanAlreadyExistsError,
)
from core.services.finalization import finalize_run_if_needed
from core.types import IngestSpanResult, PostIngestPayload, SpanIngestData
from core.validators import ValidationError, validate_span_attributes

logger = logging.getLogger("telemetry_lab")


def _json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def fingerprint_ingest_payload(data: SpanIngestData) -> str:
    """Stable SHA-256 fingerprint of the ingest payload for idempotency checks."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest_span(
    *,
    data: SpanIngestData,
    idempotency_key: str | None,
    post_ingest_hook: Callable[[str, PostIngestPayload], None],
) -> IngestSpanResult:
    validate_payload_semantics(data)
    payload_fingerprint = fingerprint_ingest_payload(data)

    with transaction.atomic():
        run, _ = Run.objects.get_or_create(
            trace_id=data["trace_id"],
            defaults={
                "agent_name": data["agent_name"],
                "status": "running",
                "start_time": data["start_time"],
            },
        )
        # Serialize completion checks and late-span rejection against concurrent writers.
        run = Run.objects.select_for_update().get(pk=run.pk)

        claim_idempotency_key(
            idempotency_key,
            payload_fingerprint=payload_fingerprint,
            span_id=data["span_id"],
        )

        if run.status == "completed":
            raise CompletedRunConflictError("run already completed")

        validate_parent_span(data=data, run=run)

        try:
            span = Span.objects.create(
                span_id=data["span_id"],
                trace_id=run,
                parent_span_id=data["parent_span_id"],
                span_type=data["span_type"],
                name=data["name"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                status_code=data["status_code"],
                attributes=data["attributes"],
            )
        except IntegrityError as exc:
            raise SpanAlreadyExistsError("span_id already exists") from exc

        run_completed = finalize_run_if_needed(
            run=run,
            is_final=data["is_final"],
            end_time=data["end_time"],
        )

        if idempotency_key:
            IdempotencyKey.objects.filter(key=idempotency_key).update(
                run_completed=run_completed,
            )

    try:
        post_ingest_hook(
            "post_ingest",
            {"span_id": str(span.span_id), "trace_id": str(data["trace_id"])},
        )
    except Exception as exc:
        # Isolate hook failures from ingestion success path,
        # but still surface them for observability.
        logger.exception(
            "Post-ingest hook failed (ingestion unaffected)",
            extra={
                "trace_id": str(data["trace_id"]),
                "span_id": str(data["span_id"]),
                "extra_fields": {
                    "failure_class": exc.__class__.__name__,
                    "failure_message": str(exc),
                },
            },
        )

    try:
        metrics.increment_spans_ingested()
    except Exception as exc:
        # Span persistence already committed; metrics must not fail the response.
        logger.exception(
            "Post-commit metrics update failed (ingestion unaffected)",
            extra={
                "trace_id": str(data["trace_id"]),
                "span_id": str(data["span_id"]),
                "extra_fields": {
                    "failure_class": exc.__class__.__name__,
                    "failure_message": str(exc),
                },
            },
        )

    return {
        "span_id": str(span.span_id),
        "run_completed": run_completed,
    }


def claim_idempotency_key(
    idempotency_key: str | None,
    *,
    payload_fingerprint: str,
    span_id: UUID,
) -> None:
    if not idempotency_key:
        return

    try:
        # Savepoint so a duplicate-key IntegrityError does not abort the outer txn.
        with transaction.atomic():
            IdempotencyKey.objects.create(
                key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                span_id=span_id,
                run_completed=False,
            )
    except IntegrityError as exc:
        existing = IdempotencyKey.objects.filter(key=idempotency_key).first()
        if existing is None:
            raise IdempotentDuplicateError(
                "duplicate idempotency key",
                span_id=str(span_id),
                run_completed=False,
            ) from exc
        if existing.payload_fingerprint != payload_fingerprint:
            raise IdempotencyConflictError(
                "idempotency key reused with a different payload"
            ) from exc
        raise IdempotentDuplicateError(
            "duplicate idempotency key",
            span_id=str(existing.span_id),
            run_completed=existing.run_completed,
        ) from exc


def validate_payload_semantics(data: SpanIngestData) -> None:
    validate_span_attributes(data["span_type"], data["attributes"])

    now = timezone.now()
    future_threshold = now + timedelta(minutes=1)
    past_threshold = now - timedelta(hours=24)

    if data["start_time"] > future_threshold:
        raise ValidationError("start_time too far in future")
    if data["end_time"] > future_threshold:
        raise ValidationError("end_time too far in future")
    if data["start_time"] < past_threshold:
        raise ValidationError("start_time too old")
    if data["end_time"] < past_threshold:
        raise ValidationError("end_time too old")


def validate_parent_span(*, data: SpanIngestData, run: Run) -> None:
    parent_span_id = data["parent_span_id"]
    if not parent_span_id:
        return

    parent_exists = Span.objects.filter(
        trace_id=run,
        span_id=parent_span_id,
    ).exists()
    if not parent_exists:
        raise ParentSpanNotFoundError("parent_span_id not found in trace")
