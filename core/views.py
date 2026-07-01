import importlib
import json
import logging
import time
import uuid as uuid_module
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast

from django.conf import settings
from django.db import connection
from django.db.models import QuerySet
from django.http import HttpResponse
from django.views.generic import DetailView, ListView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.hooks import run_hook
from core.metrics import metrics
from core.middleware.request_id import get_current_request_id
from core.models import Run, Span
from core.serializers import SpanIngestSerializer
from core.services.exceptions import (
    CompletedRunConflictError,
    IdempotentDuplicateError,
    ParentSpanNotFoundError,
    SpanAlreadyExistsError,
)
from core.services.ingestion import ingest_span
from core.types import SpanIngestData
from core.validators import ValidationError

logger = logging.getLogger("telemetry_lab")

if TYPE_CHECKING:
    BaseRunListView = ListView[Run]
    BaseRunDetailView = DetailView[Run]
else:
    BaseRunListView = ListView
    BaseRunDetailView = DetailView


class RateLimitBucket(TypedDict):
    count: int
    reset_time: float


class SpanTreeNode(TypedDict):
    span: Span
    children: list["SpanTreeNode"]
    duration_ms: float
    attributes_json: str


# In-memory rate limiter for the dev/portfolio scope.
# Does NOT survive gunicorn worker forks or multi-process deployments.
# In production this would be backed by Redis or a cache with atomic counters.
_rate_limits: defaultdict[str, RateLimitBucket] = defaultdict(
    lambda: {"count": 0, "reset_time": 0.0}
)


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

    def post(self, request: Request) -> Response:
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

        serializer = SpanIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = cast(SpanIngestData, serializer.validated_data)

        try:
            result = ingest_span(
                data=data,
                idempotency_key=idempotency_key,
                post_ingest_hook=run_hook,
            )
        except IdempotentDuplicateError:
            return Response({"span_id": "duplicate"}, status=200)
        except ParentSpanNotFoundError:
            return Response({"error": "parent_span_id not found in trace"}, status=400)
        except SpanAlreadyExistsError:
            return Response({"error": "span_id already exists"}, status=400)
        except CompletedRunConflictError:
            return Response({"error": "run already completed"}, status=409)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        logger.info(
            "Span ingested",
            extra={
                "trace_id": str(data["trace_id"]),
                "span_id": result["span_id"],
                "request_id": get_current_request_id(),
                "extra_fields": {"span_type": data["span_type"]},
            },
        )

        response_data = {"span_id": result["span_id"]}
        if result["run_completed"]:
            response_data["run_status"] = "completed"

        request_id = get_current_request_id() or str(uuid_module.uuid4())
        return Response(
            response_data,
            status=201,
            headers={"X-Request-ID": request_id, "X-Span-ID": result["span_id"]},
        )


def build_span_tree(spans: list[Span]) -> list[SpanTreeNode]:
    span_map: dict[str, SpanTreeNode] = {
        str(span.span_id): {
            "span": span,
            "children": [],
            "duration_ms": (span.end_time - span.start_time).total_seconds() * 1000,
            "attributes_json": json.dumps(span.attributes, indent=2, sort_keys=True),
        }
        for span in spans
    }
    roots: list[SpanTreeNode] = []
    for item in span_map.values():
        pid = str(item["span"].parent_span_id) if item["span"].parent_span_id else None
        if pid and pid in span_map:
            span_map[pid]["children"].append(item)
        else:
            roots.append(item)
    return roots


def latency_ms(start_time: datetime, end_time: datetime | None) -> float | None:
    if not end_time:
        return None
    return (end_time - start_time).total_seconds() * 1000


class RunListView(BaseRunListView):
    model = Run
    template_name = "core/run_list.html"
    context_object_name = "runs"

    def get_queryset(self) -> QuerySet[Run]:
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

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["run_rows"] = [
            {"run": run, "latency_ms": latency_ms(run.start_time, run.end_time)}
            for run in ctx["runs"]
        ]
        return ctx


class RunDetailView(BaseRunDetailView):
    model = Run
    template_name = "core/run_detail.html"
    pk_url_kwarg = "trace_id"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        spans = list(self.object.spans.order_by("start_time"))
        ctx["span_tree"] = build_span_tree(spans)
        ctx["span_count"] = len(spans)
        ctx["evaluation"] = getattr(self.object, "evaluation", None)
        ctx["run_latency_ms"] = latency_ms(self.object.start_time, self.object.end_time)
        return ctx


class HealthCheckView(APIView):
    def get(self, request: Request) -> Response:
        checks: dict[str, str] = {}

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
    def get(self, request: Request) -> HttpResponse:
        return HttpResponse(
            metrics.get_prometheus_text(),
            content_type="text/plain; charset=utf-8",
        )
