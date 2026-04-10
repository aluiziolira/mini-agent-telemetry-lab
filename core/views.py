import importlib
import json
import logging
import time
import uuid as uuid_module
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, connection
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import DetailView, ListView
from rest_framework.response import Response
from rest_framework.views import APIView

from core.hooks import run_hook
from core.metrics import metrics
from core.middleware.request_id import get_current_request_id
from core.models import IdempotencyKey, Run, Span
from core.serializers import SpanIngestSerializer
from core.validators import ValidationError, validate_span_attributes

logger = logging.getLogger("telemetry_lab")

_rate_limits = defaultdict(lambda: {"count": 0, "reset_time": 0})


def _check_rate_limit(api_key: str, limit: int = 100, window: int = 60) -> bool:
    now = time.time()
    data = _rate_limits[api_key]
    if now - data["reset_time"] > window:
        data["reset_time"] = now
        data["count"] = 0
    if data["count"] >= limit:
        return False
    data["count"] += 1
    return True


class IngestSpanView(APIView):
    """
    Ingest span endpoint (API v1).

    Versioning strategy: URL path versioning (/api/v1/ingest/span/)
    Breaking changes (new required fields, removed fields, changed types)
    trigger a new version (/api/v2/ingest/span/).
    """

    def post(self, request):
        if request.headers.get("X-API-Key") != settings.INGEST_API_KEY:
            return Response({"error": "forbidden"}, status=403)

        api_key = request.headers.get("X-API-Key", "")
        if not _check_rate_limit(api_key):
            return Response(
                {"error": "rate limit exceeded"},
                status=429,
                headers={"Retry-After": "60"},
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            if IdempotencyKey.objects.filter(key=idempotency_key).exists():
                return Response({"span_id": "duplicate"}, status=200)

        serializer = SpanIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        try:
            validate_span_attributes(data.get("span_type"), data.get("attributes", {}))
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)

        now = timezone.now()
        future_threshold = now + timedelta(minutes=1)
        past_threshold = now - timedelta(hours=24)

        if data["start_time"] > future_threshold:
            return Response({"error": "start_time too far in future"}, status=400)
        if data["end_time"] > future_threshold:
            return Response({"error": "end_time too far in future"}, status=400)
        if data["start_time"] < past_threshold:
            return Response({"error": "start_time too old"}, status=400)
        if data["end_time"] < past_threshold:
            return Response({"error": "end_time too old"}, status=400)

        if data.get("parent_span_id"):
            parent_exists = Span.objects.filter(
                trace_id__trace_id=data["trace_id"],
                span_id=data["parent_span_id"],
            ).exists()
            if not parent_exists:
                return Response({"error": "parent_span_id not found in trace"}, status=400)

        if idempotency_key:
            IdempotencyKey.objects.create(key=idempotency_key)

        run, _ = Run.objects.get_or_create(
            trace_id=data["trace_id"],
            defaults={
                "agent_name": data.get("agent_name", "unknown"),
                "status": "running",
                "start_time": data["start_time"],
            },
        )

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
        except IntegrityError:
            return Response({"error": "span_id already exists"}, status=400)

        run_hook(
            "post_ingest",
            {"span_id": str(span.span_id), "trace_id": str(data["trace_id"])},
        )

        metrics.increment_spans_ingested()
        logger.info(
            "Span ingested",
            extra={
                "trace_id": str(data["trace_id"]),
                "span_id": str(data["span_id"]),
                "request_id": get_current_request_id(),
                "extra_fields": {"span_type": data["span_type"]},
            },
        )

        response_data = {"span_id": str(span.span_id)}

        if data.get("is_final"):
            spans = Span.objects.filter(trace_id=run)
            total_tokens = sum(
                s.attributes.get("prompt_tokens", 0) + s.attributes.get("completion_tokens", 0)
                for s in spans
            )
            total_cost = Decimal(total_tokens) * Decimal("0.000002")
            run.status = "completed"
            run.end_time = data["end_time"]
            run.total_tokens = total_tokens
            run.total_cost = total_cost
            run.save()
            response_data["run_status"] = "completed"

        request_id = get_current_request_id() or str(uuid_module.uuid4())
        return Response(
            response_data,
            status=201,
            headers={"X-Request-ID": request_id, "X-Span-ID": str(span.span_id)},
        )


def build_span_tree(spans):
    span_map = {
        str(span.span_id): {
            "span": span,
            "children": [],
            "duration_ms": (span.end_time - span.start_time).total_seconds() * 1000,
            "attributes_json": json.dumps(span.attributes, indent=2, sort_keys=True),
        }
        for span in spans
    }
    roots = []
    for item in span_map.values():
        pid = str(item["span"].parent_span_id) if item["span"].parent_span_id else None
        if pid and pid in span_map:
            span_map[pid]["children"].append(item)
        else:
            roots.append(item)
    return roots


def latency_ms(start_time, end_time):
    if not end_time:
        return None
    return (end_time - start_time).total_seconds() * 1000


class RunListView(ListView):
    model = Run
    template_name = "core/run_list.html"
    context_object_name = "runs"

    def get_queryset(self):
        sort = self.request.GET.get("sort", "-start_time")
        allowed = [
            "start_time",
            "-start_time",
            "total_cost",
            "-total_cost",
            "eval_score",
            "-eval_score",
        ]
        if sort not in allowed:
            sort = "-start_time"
        return Run.objects.select_related("evaluation").order_by(sort)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["run_rows"] = [
            {"run": run, "latency_ms": latency_ms(run.start_time, run.end_time)}
            for run in ctx["runs"]
        ]
        return ctx


class RunDetailView(DetailView):
    model = Run
    template_name = "core/run_detail.html"
    pk_url_kwarg = "trace_id"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        spans = list(self.object.spans.order_by("start_time"))
        ctx["span_tree"] = build_span_tree(spans)
        ctx["span_count"] = len(spans)
        ctx["evaluation"] = getattr(self.object, "evaluation", None)
        ctx["run_latency_ms"] = latency_ms(self.object.start_time, self.object.end_time)
        return ctx


class HealthCheckView(APIView):
    def get(self, request):
        checks = {}

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {str(e)}"

        try:
            importlib.import_module("huey.api")
            checks["queue"] = "ok"
        except Exception as e:
            checks["queue"] = f"error: {str(e)}"

        status = "healthy" if all(v == "ok" for v in checks.values()) else "unhealthy"
        http_status = 200 if status == "healthy" else 503

        return Response({"status": status, "checks": checks}, status=http_status)


class MetricsView(APIView):
    def get(self, request):
        return HttpResponse(
            metrics.get_prometheus_text(),
            content_type="text/plain; charset=utf-8",
        )
