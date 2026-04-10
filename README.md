# Mini Agent Telemetry Lab

Most AI demo projects generate text just fine, but few explain *why* a response was slow, wrong, or brittle. Without span-level telemetry, engineering teams cannot isolate whether failures come from model latency, tool execution, prompt construction, or orchestration logic.

**Mini Agent Telemetry Lab** is a backend observability system that turns opaque LLM-agent behavior into traceable execution data and scoreable quality signals. It makes every agent run inspectable, measurable, and evaluable through a single backend system built for debugging and iterative quality improvement.

## Architecture & Data Model

This project optimizes for engineering signal density and iteration speed. I chose a Django monolith with DRF ingestion to enforce schema boundaries at ingress.

* **Data Model:** An OTel inspired telemetry model maps `Run` (trace) and `Span` (step). A single SQL datastore with a JSON field on `Span.attributes` balances relational guarantees (indexes, transactional integrity) with telemetry flexibility (variable tool and LLM payloads).
* **Ingestion Boundary:** DRF serializers validate incoming spans before persistence. They reject malformed payloads before they reach storage (`core/serializers.py`, `core/views.py`).
* **Asynchronous Quality Loop:** The system evaluates completed runs with an LLM judge and stores explainable scores (`core/tasks.py`).
* **Queueing Choice:** I use **SqlHuey** for the asynchronous evaluation path. Ingestion persists spans first, and a SqlHuey worker later computes quality scores. This is the correct queue choice here. It cuts infrastructure overhead for a portfolio-scale system by reusing the PostgreSQL database, eliminating the need for a separate Redis or RabbitMQ instance.
* **Framework Agnostic Instrumentation:** A custom Python tracer emits spans over HTTP without Django coupling. This allows usage from non-Django agent clients (`sdk/tracer.py`).

## Technical Decisions & Tradeoffs

The architecture demonstrates production reasoning under constraint. The system captures first, validates early, computes asynchronously, and preserves enough context to explain system behavior under failure.

| Tradeoff | Decision | Defensibility |
|---|---|---|
| **Latency vs. Consistency** | Synchronous span POST in tracer and fail open on emit error. | Keeps instrumentation simple. Ensures agent execution is never blocked by telemetry transport failures. |
| **SQL vs. NoSQL** | Single SQL datastore with JSON fields. | Preserves relational tooling and query power while retaining flexible span payload schemas. Avoids the integration drag of a separate document database. |
| **Operational Simplicity** | SqlHuey and PostgreSQL backend over Celery and Redis. | Perfectly scoped for a portfolio project. It provides reliable queueing without massive infrastructure overhead. A documented production migration path exists in `core/tasks.py`. |
| **Strict Schema vs. Adaptability** | Fixed top level span fields plus a JSON `attributes` payload. | Protects core query patterns (tokens, cost) while allowing rapid evolution of model or tool metadata capture. |

### Backend Best Practices

* **Boundary validation first:** DRF serializer rejects invalid telemetry payloads before DB writes.
* **Deterministic numeric handling:** Uses `Decimal` for cost math to avoid floating point drift.
* **Separation of concerns:** Ingestion, evaluation, and rendering are isolated in distinct modules.
* **Error observability:** Span exceptions are marked as `ERROR`. The exception message is stored in span attributes rather than failing silently.

## Verified Behavior

The project includes automated tests to prove the domain logic works.

| Proof Point | Evidence | Why It Matters |
|---|---|---|
| **Token aggregation** | `tests/test_lifecycle.py` asserts `run.total_tokens == 50` after two spans (`10+5` and `20+15`). | Confirms deterministic rollup logic for run-level metrics. It is not a UI-only estimate. |
| **Reproducible costs** | `core/views.py` computes `total_cost = Decimal(total_tokens) * Decimal("0.000002")`. | Keeps cost estimates transparent and auditable for completed runs. |
| **Nested span reconstruction** | `tests/test_lifecycle.py` validates root to child to grandchild depth. | Proves parent/child telemetry can be reconstructed for root cause analysis. |
| **Persisted quality signals** | `core/tasks.py` writes `Evaluation` and denormalizes `Run.eval_score`. | Demonstrates an end-to-end observe and evaluate loop. |
| **Recruiter-relevant KPIs** | `core/models.py` defines `total_tokens`, `total_cost`, and `eval_score`. | Shows the system targets performance, cost, and quality triage in one data plane. |

### Local Verification

Run the test suite to verify ingestion and rollup logic:

```bash
uv run pytest -q tests
```

Expected proof signals from this suite:
1. Run creation from first span (`status="running"`).
2. Completion on final span (`status="completed"`).
3. Token rollup at `50` for the controlled test fixture.
4. Span tree depth integrity for nested execution paths.

## Demo Agents

There are now two small agent entry points that share the same tracer boundary and ingestion path:

```bash
just agent
just raw-agent
```

* `demo_agent.py` shows a richer research flow with live tools and an LLM call.
* `raw_sdk_agent.py` is a standalone hand-rolled Python agent that uses the same `sdk.tracer.Tracer` directly and still posts spans to `/api/v1/ingest/span/`.

That side-by-side contrast is the framework-agnostic proof: two different agent styles, one telemetry pipeline.
