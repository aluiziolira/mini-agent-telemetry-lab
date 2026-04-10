from django.urls import path

from core.views import IngestSpanView, RunDetailView, RunListView

urlpatterns = [
    path("api/v1/ingest/span/", IngestSpanView.as_view(), name="ingest_span"),
    path("runs/", RunListView.as_view(), name="run_list"),
    path("runs/<uuid:trace_id>/", RunDetailView.as_view(), name="run_detail"),
]
