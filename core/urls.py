from django.urls import path

from core.views import (
    HealthCheckView,
    IngestSpanView,
    MetricsView,
    RunDetailView,
    RunListView,
)

urlpatterns = [
    path("api/v1/ingest/span/", IngestSpanView.as_view(), name="ingest_span"),
    path("health/", HealthCheckView.as_view(), name="health_check"),
    path("metrics/", MetricsView.as_view(), name="metrics"),
    path("runs/", RunListView.as_view(), name="run_list"),
    path("runs/<uuid:trace_id>/", RunDetailView.as_view(), name="run_detail"),
]
