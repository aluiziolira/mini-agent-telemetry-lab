import json
from decimal import Decimal

from django.conf import settings
from django.views.generic import DetailView, ListView
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Run, Span
from core.serializers import SpanIngestSerializer


class IngestSpanView(APIView):
    def post(self, request):
        if request.headers.get("X-API-Key") != settings.INGEST_API_KEY:
            return Response({"error": "forbidden"}, status=403)

        serializer = SpanIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        run, _ = Run.objects.get_or_create(
            trace_id=data["trace_id"],
            defaults={
                "agent_name": data.get("agent_name", "unknown"),
                "status": "running",
                "start_time": data["start_time"],
            },
        )

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

        response_data = {"span_id": str(span.span_id)}

        if data.get("is_final"):
            spans = Span.objects.filter(trace_id=run)
            total_tokens = sum(
                s.attributes.get("prompt_tokens", 0)
                + s.attributes.get("completion_tokens", 0)
                for s in spans
            )
            total_cost = Decimal(total_tokens) * Decimal("0.000002")
            run.status = "completed"
            run.end_time = data["end_time"]
            run.total_tokens = total_tokens
            run.total_cost = total_cost
            run.save()
            response_data["run_status"] = "completed"

        return Response(response_data, status=201)


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
