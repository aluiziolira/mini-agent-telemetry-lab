# Mini Agent Telemetry Lab

## Project Title
**Mini Agent Telemetry Lab** is a backend observability system that turns opaque LLM-agent behavior into traceable execution data and scoreable quality signals.

- **OTel-inspired telemetry model:** maps `Run` (trace) + `Span` (step) + flexible JSON attributes for queryable debugging (`core/models.py`).
- **Validated ingestion boundary:** validates incoming spans with DRF serializers before persistence, rejecting malformed payloads before they reach storage (`core/serializers.py`, `core/views.py`).
- **Asynchronous quality loop:** evaluates completed runs with an LLM judge and stores explainable scores (`core/tasks.py`, `core/management/commands/eval_pending.py`).
- **Framework-agnostic instrumentation:** custom Python tracer emits spans over HTTP without Django coupling, making it usable from non-Django agent clients (`sdk/tracer.py`).
- **Operationally constrained architecture:** Django + a single SQL datastore + Huey keeps infrastructure intentionally minimal while preserving production-relevant patterns (`telemetry_lab/settings.py`, `core/tasks.py`).

## Verified Behavior

### Metrics & Behavioral Evidence

| Proof Point | Evidence | Why It Matters |
|---|---|---|
| **Token aggregation correctness** | `tests/test_ingestion.py` asserts `run.total_tokens == 50` after two spans (`10+5` and `20+15`) | Confirms deterministic rollup logic for run-level metrics, not a UI-only estimate. |
| **Cost computation is explicit and reproducible** | `core/views.py` computes `total_cost = Decimal(total_tokens) * Decimal("0.000002")` | Keeps cost estimates transparent and auditable for completed runs. |
| **Nested span reconstruction works** | `tests/test_ingestion.py` validates root → child → grandchild depth via `build_span_tree()` | Proves parent/child telemetry can be reconstructed for root-cause analysis. |
| **Evaluation pipeline persists quality signals** | `core/tasks.py` writes `Evaluation(correctness_score, helpfulness_score, aggregate_score, reasoning)` and denormalizes `Run.eval_score` | Demonstrates an end-to-end observe + evaluate loop instead of raw logging only. |
| **Schema supports recruiter-relevant KPIs** | `core/models.py` defines `total_tokens`, `total_cost`, `eval_score`, and per-span JSON attributes | Shows the system was designed for performance/cost/quality triage in one data plane. |

### Local Verification Log (example command)

```bash
pytest -q tests/test_ingestion.py
```

Expected proof signals from this suite:
- Run creation from first span (`status="running"`)
- Completion on final span (`status="completed"`)
- Token rollup at `50` for the controlled test fixture
- Span tree depth integrity for nested execution paths

## Problem Statement
Most AI demo projects can generate text, but few can explain *why* a response was slow, wrong, or brittle. Without span-level telemetry, engineering teams cannot isolate whether failures came from model latency, tool execution, prompt construction, or orchestration logic. This project addresses that gap by making every agent run inspectable, measurable, and evaluable through a single backend system built for debugging and iterative quality improvement.

## Technical Decisions
I chose a Django monolith with DRF ingestion over a split FastAPI + frontend stack because this project optimizes for engineering signal density and iteration speed. DRF serializers enforce schema boundaries at ingress, Django Admin provides immediate data introspection during debugging, and server-rendered templates keep the observability UI inspectable with zero frontend build complexity. A single SQL datastore was selected, with a JSON field on `Span.attributes`, to balance relational guarantees (indexes, transactional integrity, joins) with telemetry flexibility (variable tool/LLM payloads). This avoids the integration drag of introducing a separate document database while preserving evolvable span schemas.

The evaluation path is intentionally asynchronous: ingestion persists spans first, and a Huey worker later computes quality scores from completed traces. This separates write-path reliability from judge latency, so observability data lands even when external LLM calls are slow or unavailable. The custom tracer takes a fail-open stance: emission failures are logged and do not raise back into the agent, while in-span exceptions are marked as `ERROR` and store `error_message` in span attributes. Together, these choices create a pragmatic architecture that demonstrates production reasoning under constraint: capture first, validate early, compute asynchronously, and preserve enough context to explain system behavior under failure.

### Explicit Trade-offs

| Trade-off | Decision | Why This Is Defensible |
|---|---|---|
| **Latency vs. Consistency** | Synchronous span POST in tracer + fail-open on emit error | Keeps instrumentation simple and immediate, while ensuring agent execution is never blocked by telemetry transport failures. |
| **SQL vs. NoSQL** | Single SQL datastore + JSON field instead of MongoDB | Preserves relational tooling and query power while retaining flexible span payload schemas. |
| **Operational simplicity vs. queue throughput ceiling** | Huey + PostgreSQL backend instead of Celery + Redis/RabbitMQ | Cuts infra overhead for a portfolio-scale system, with a documented production migration path in `core/tasks.py`. |
| **Strict schema vs. adaptable telemetry** | Fixed top-level span fields + JSON `attributes` payload | Protects core query patterns while allowing rapid evolution of model/tool metadata capture. |

### Python / Backend Best Practices Applied

- **Boundary validation first:** DRF serializer rejects invalid telemetry payloads before DB writes.
- **Deterministic numeric handling:** uses `Decimal` for cost math to avoid floating-point drift.
- **Separation of concerns:** ingestion, evaluation, and rendering are isolated in distinct modules.
- **Error observability over silent failure:** span exceptions are marked as `ERROR`, and the exception message is stored in span attributes.
- **Tested domain behavior:** ingestion lifecycle and span-tree reconstruction are covered in targeted pytest cases.
