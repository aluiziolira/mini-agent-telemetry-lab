"""Contract tests for API schema stability and enum consistency.

These tests verify that the API contract remains stable over time,
catching breaking changes before they affect clients.
"""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Span


@pytest.mark.django_db
def test_ingestion_response_schema(client, settings):
    """Verify JSON response structure for span ingestion hasn't changed.

    Contract: Response must contain 'span_id' field with string value.
    For non-final spans, response should NOT contain 'run_status'.
    """
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
    data = response.json()
    assert "span_id" in data
    assert isinstance(data["span_id"], str)
    assert "run_status" not in data


@pytest.mark.django_db
def test_final_span_response_schema_includes_run_status(client, settings):
    """Verify final span response includes run_status field.

    Contract: When is_final=True, response must include 'run_status' field
    with value 'completed' after run aggregation.
    """
    settings.INGEST_API_KEY = "dev-ingest-key"
    trace_id = uuid.uuid4()
    now = timezone.now()

    client.post(
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
    data = response.json()
    assert "span_id" in data
    assert "run_status" in data
    assert data["run_status"] == "completed"


def test_span_types_enum():
    """Verify span_type choices are consistent with model definition.

    Contract: span_type choices must match OTel-inspired semantic conventions.
    Changes to these choices are breaking changes for API clients.
    """
    expected = [("llm", "llm"), ("tool", "tool"), ("chain", "chain")]
    assert Span.SPAN_TYPE_CHOICES == expected


def test_status_codes_enum():
    """Verify status_code choices are consistent with model definition.

    Contract: status_code uses OK/ERROR pattern aligned with OTel conventions.
    """
    expected = [("OK", "OK"), ("ERROR", "ERROR")]
    assert Span.STATUS_CODE_CHOICES == expected
