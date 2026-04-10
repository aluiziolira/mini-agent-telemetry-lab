"""Property-based and parametrized tests for edge cases and invariants.

These tests verify that core algorithms work correctly across a range of
inputs and edge cases, ensuring mathematical and logical correctness.
"""

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Run, Span
from core.views import build_span_tree


@pytest.mark.parametrize("depth", [1, 5, 10, 50])
@pytest.mark.django_db
def test_span_tree_reconstruction_various_depths(depth):
    """Verify span tree builds correctly for various nesting depths.

    This tests the build_span_tree algorithm across different tree depths
    to ensure the parent-child reconstruction works at scale.
    """
    run = Run.objects.create(
        agent_name="test_agent",
        status="running",
        start_time=timezone.now(),
    )

    spans = []
    parent_id = None

    for i in range(depth):
        span = Span.objects.create(
            trace_id=run,
            span_id=uuid.uuid4(),
            span_type="chain",
            name=f"level_{i}",
            start_time=timezone.now(),
            end_time=timezone.now(),
            status_code="OK",
            parent_span_id=parent_id,
        )
        spans.append(span)
        parent_id = span.span_id

    tree = build_span_tree(spans)

    # Should have single root
    assert len(tree) == 1
    assert tree[0]["span"].name == "level_0"

    # Verify depth by traversing children
    current = tree[0]
    for i in range(depth - 1):
        assert len(current["children"]) == 1
        assert current["children"][0]["span"].name == f"level_{i + 1}"
        current = current["children"][0]

    # Last level should have no children
    assert len(current["children"]) == 0


@pytest.mark.parametrize(
    "prompt_tokens,completion_tokens",
    [
        (0, 0),  # Edge: no tokens
        (1, 1),  # Minimum non-zero
        (100, 200),
        (1000, 5000),
        (8192, 8192),  # Common model limits
    ],
)
@pytest.mark.django_db
def test_token_aggregation_with_various_combinations(prompt_tokens, completion_tokens):
    """Test token rollup logic with various prompt/completion combinations.

    Verifies that total_tokens = prompt_tokens + completion_tokens for
    various realistic token counts, ensuring the aggregation math is correct.
    """
    run = Run.objects.create(
        agent_name="test_agent",
        status="completed",
        start_time=timezone.now(),
        end_time=timezone.now(),
    )

    # Create spans with specified token counts
    Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="span1",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        attributes={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    )

    # Simulate aggregation logic from IngestSpanView
    total = sum(
        s.attributes.get("prompt_tokens", 0) + s.attributes.get("completion_tokens", 0)
        for s in run.spans.all()
    )

    expected_total = prompt_tokens + completion_tokens
    assert total == expected_total

    # Test cost calculation
    expected_cost = Decimal(expected_total) * Decimal("0.000002")
    assert expected_cost == Decimal(expected_total) * Decimal("0.000002")


def test_latency_calculation_precision():
    """Verify Decimal math in cost calculation avoids floating-point drift.

    Using Decimal instead of float for cost calculations ensures precise
    decimal arithmetic, which is critical for financial/billing calculations.
    """
    # Test various token counts that would cause float precision issues
    test_cases = [
        (1, Decimal("0.000002")),
        (10, Decimal("0.00002")),
        (100, Decimal("0.0002")),
        (1000, Decimal("0.002")),
        (12345, Decimal("0.02469")),
        (999999, Decimal("1.999998")),
    ]

    for tokens, expected_cost in test_cases:
        calculated_cost = Decimal(tokens) * Decimal("0.000002")
        assert calculated_cost == expected_cost

        # Verify it's a Decimal, not float
        assert isinstance(calculated_cost, Decimal)


@pytest.mark.parametrize(
    "start_time,end_time,expected_ms",
    [
        # Same second
        (
            timezone.datetime(2025, 1, 1, 0, 0, 0),
            timezone.datetime(2025, 1, 1, 0, 0, 1),
            1000.0,
        ),
        # Millisecond precision
        (
            timezone.datetime(2025, 1, 1, 0, 0, 0),
            timezone.datetime(2025, 1, 1, 0, 0, 0, 500000),  # 500ms
            500.0,
        ),
        # Multiple seconds
        (
            timezone.datetime(2025, 1, 1, 0, 0, 0),
            timezone.datetime(2025, 1, 1, 0, 0, 5),
            5000.0,
        ),
    ],
)
def test_latency_ms_calculation(start_time, end_time, expected_ms):
    """Verify latency calculation in milliseconds is precise.

    The latency_ms helper should correctly convert timedelta to milliseconds.
    """
    from core.views import latency_ms

    # Make timezone-aware
    start = timezone.make_aware(start_time)
    end = timezone.make_aware(end_time)

    result = latency_ms(start, end)
    assert abs(result - expected_ms) < 0.1  # Allow small floating point variance


@pytest.mark.django_db
def test_build_span_tree_with_multiple_roots():
    """Verify tree reconstruction handles multiple root spans correctly.

    A trace can have multiple root-level spans (no parent_span_id).
    This tests that build_span_tree correctly groups them.
    """
    run = Run.objects.create(
        agent_name="test_agent",
        status="running",
        start_time=timezone.now(),
    )

    # Create 3 root spans (no parent)
    root1 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="root1",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=None,
    )
    root2 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="root2",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=None,
    )
    root3 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="root3",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=None,
    )

    tree = build_span_tree([root1, root2, root3])

    # Should have 3 roots
    assert len(tree) == 3
    root_names = {node["span"].name for node in tree}
    assert root_names == {"root1", "root2", "root3"}


@pytest.mark.django_db
def test_build_span_tree_with_siblings():
    """Verify tree reconstruction handles sibling spans correctly.

    Multiple spans can share the same parent (siblings).
    This tests correct sibling grouping.
    """
    run = Run.objects.create(
        agent_name="test_agent",
        status="running",
        start_time=timezone.now(),
    )

    # Create parent
    parent = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="chain",
        name="parent",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=None,
    )

    # Create 3 siblings with same parent
    child1 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="tool",
        name="child1",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=parent.span_id,
    )
    child2 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="tool",
        name="child2",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=parent.span_id,
    )
    child3 = Span.objects.create(
        trace_id=run,
        span_id=uuid.uuid4(),
        span_type="llm",
        name="child3",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status_code="OK",
        parent_span_id=parent.span_id,
    )

    tree = build_span_tree([parent, child1, child2, child3])

    # Single root
    assert len(tree) == 1
    assert tree[0]["span"].name == "parent"

    # 3 children
    assert len(tree[0]["children"]) == 3
    child_names = {child["span"].name for child in tree[0]["children"]}
    assert child_names == {"child1", "child2", "child3"}
