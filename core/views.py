from decimal import Decimal

from django.conf import settings
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


