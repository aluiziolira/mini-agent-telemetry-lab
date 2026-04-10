from django.urls import path

from core.views import IngestSpanView

urlpatterns = [
    path("api/v1/ingest/span/", IngestSpanView.as_view(), name="ingest_span"),
]
