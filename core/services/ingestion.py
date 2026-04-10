from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.metrics import metrics
from core.models import IdempotencyKey, Run, Span
from core.services.exceptions import (
    CompletedRunConflictError,
    IdempotentDuplicateError,
    ParentSpanNotFoundError,
    SpanAlreadyExistsError,
)
from core.services.finalization import finalize_run_if_needed
from core.validators import ValidationError, validate_span_attributes


def ingest_span(*, data: dict, idempotency_key: str | None, post_ingest_hook):
    validate_payload_semantics(data)

    with transaction.atomic():
        run, _ = Run.objects.get_or_create(
            trace_id=data["trace_id"],
            defaults={
                "agent_name": data.get("agent_name", "unknown"),
                "status": "running",
                "start_time": data["start_time"],
            },
        )

        claim_idempotency_key(idempotency_key)

        if run.status == "completed":
            raise CompletedRunConflictError("run already completed")

        validate_parent_span(data=data, run=run)

        try:
            span = Span.objects.create(
                span_id=data["span_id"],
                trace_id=run,
                parent_span_id=data.get("parent_span_id"),
                span_type=data["span_type"],
                name=data["name"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                status_code=data["status_code"],
                attributes=data.get("attributes", {}),
            )
        except IntegrityError as exc:
            raise SpanAlreadyExistsError("span_id already exists") from exc

        run_completed = finalize_run_if_needed(
            run=run,
            is_final=data.get("is_final", False),
            end_time=data["end_time"],
        )

    try:
        post_ingest_hook(
            "post_ingest",
            {"span_id": str(span.span_id), "trace_id": str(data["trace_id"])},
        )
    except Exception:
        # Isolate hook failures from ingestion success path.
        pass

    metrics.increment_spans_ingested()

    return {
        "span_id": str(span.span_id),
        "run_completed": run_completed,
    }


def claim_idempotency_key(idempotency_key: str | None) -> None:
    if not idempotency_key:
        return

    try:
        IdempotencyKey.objects.create(key=idempotency_key)
    except IntegrityError as exc:
        raise IdempotentDuplicateError("duplicate idempotency key") from exc


def validate_payload_semantics(data: dict) -> None:
    validate_span_attributes(data.get("span_type"), data.get("attributes", {}))

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


def validate_parent_span(*, data: dict, run: Run) -> None:
    parent_span_id = data.get("parent_span_id")
    if not parent_span_id:
        return

    parent_exists = Span.objects.filter(
        trace_id=run,
        span_id=parent_span_id,
    ).exists()
    if not parent_exists:
        raise ParentSpanNotFoundError("parent_span_id not found in trace")
