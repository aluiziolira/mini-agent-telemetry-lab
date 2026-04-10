"""Contract tests for the API and model enums reviewers rely on."""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Span


@pytest.mark.django_db
def test_non_final_ingestion_response_schema_stays_minimal(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    now = timezone.now()

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "name": "test_span",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response.status_code == 201
    assert response.json() == {"span_id": response.json()["span_id"]}
    assert isinstance(response.json()["span_id"], str)


@pytest.mark.django_db
def test_final_ingestion_response_schema_includes_completed_status(client, settings):
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    root_response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "root",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )
    assert root_response.status_code == 201

    response = client.post(
        reverse("ingest_span"),
        data={
            "span_id": str(uuid.uuid4()),
            "trace_id": str(trace_id),
            "name": "final",
            "span_type": "chain",
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "status_code": "OK",
            "is_final": True,
        },
        content_type="application/json",
        headers={"X-API-Key": "dev-ingest-key"},
    )

    assert response.status_code == 201
    assert response.json()["run_status"] == "completed"
    assert isinstance(response.json()["span_id"], str)


def test_span_type_enum_remains_stable():
    assert Span.SPAN_TYPE_CHOICES == [
        ("llm", "llm"),
        ("tool", "tool"),
        ("chain", "chain"),
    ]


def test_status_code_enum_remains_stable():
    assert Span.STATUS_CODE_CHOICES == [("OK", "OK"), ("ERROR", "ERROR")]
