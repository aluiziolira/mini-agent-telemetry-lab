# Mini Agent Telemetry Lab

A backend observability system that turns opaque LLM-agent behavior into traceable execution data and scoreable quality signals, built for debugging and iterative quality improvement.

**Core Strategies:**
- **Boundary-First Ingestion:** Validates telemetry payloads strictly at the HTTP boundary before persistence.
- **Operationally Safe Semantics:** Ensures duplicate ingestion is idempotent, completed runs are immutable, and system failures are made inspectable.
- **Asynchronous Accountability:** Evaluates completed traces asynchronously, persisting lifecycle state rather than treating background work as a black box.
- **Framework-Agnostic Instrumentation:** Employs a decoupled tracer boundary reusable across disparate bare-Python or framework-based agent implementations.

## Benchmark Proof

The system generates inspectable evidence of execution steps, retries, timing, cost, and quality signals in one backend.

**Live Agent Execution Summary:**
```text
🎯 LIVE DEMO EXECUTION SUMMARY
===============================
Showing latest 2 completed run(s).

Run 1: raw_sdk_briefing_agent
  trace_id: 81bcd730-9380-4d69-8cbd-a2980772e9e3
  summary: status=completed | spans=5 | latency=184.4ms | tokens=91 | cost=$0.0002 | eval_score=3.50

Run 2: research_analyst
  trace_id: e419408a-ea8a-4a1a-974e-348a63d267e5
  summary: status=completed | spans=6 | latency=10799.5ms | tokens=395 | cost=$0.0008 | eval_score=4.50
```

**Failure-and-Retry Operational Evidence:**
```text
3. web_search [tool ERROR] duration=<0.1ms | attempt=1/2 | error=simulated search timeout
4. web_search [tool OK] duration=1587.4ms | attempt=2/2
```
*(A live view of `/runs/` dashboard captures full parent/child trace depths for root cause analysis).*

## Problem Statement

Most AI demo projects and initial generative AI deployments successfully generate text, but completely fail to explain *why* a response was slow, factually incorrect, or brittle. When an agent pipeline misbehaves, engineering teams are left sifting through unstructured console logs. Without structured span-level telemetry, teams cannot isolate whether system degradation stems from upstream model latency, a malformed tool execution payload, degraded prompt construction, or flawed orchestration logic. This observability gap prevents iterative quality improvement and turns maintenance into guesswork.

## Technical Decisions

Designing this system required optimizing for engineering signal density and iteration speed without succumbing to infrastructure bloat. I structured the application as a Django monolith using Django REST Framework (DRF) to enforce strict schema boundaries at ingress. This ensures that malformed telemetry payloads from erratic agent clients are aggressively rejected before they ever reach the persistence layer. To support an asynchronous quality loop, I implemented an LLM-as-a-judge evaluator that operates on completed runs. Rather than defaulting to a heavy Celery/Redis queueing stack—which would introduce unnecessary operational drag for a portfolio-scale application—I strategically adopted SqlHuey. This approach achieves reliable, asynchronous job queueing directly on top of the existing PostgreSQL instance, retaining the ability to compute quality scores without expanding the infrastructure footprint.

Deliberate constraints frame the core trade-offs. **Latency vs. Consistency:** I prioritized synchronous, fail-open HTTP API span emission for the instrumentation SDK; this guarantees that agents are never blocked by telemetry transport failures, prioritizing the primary workload over absolute observability consistency. **SQL vs. NoSQL:** While agent trace payloads (tools, prompts) are highly variable, I opted for a single PostgreSQL datastore utilizing a `JSONB` field for `Span.attributes` rather than integrating a discrete document database. This preserves robust relational guarantees (foreign keys, transactional integrity for cost/token rollups) while accommodating adaptable payload schemas. Throughout the stack, Python backend best practices are rigorously applied: financial math utilizes deterministic `Decimal` types to prevent floating-point drift, duplicate ingestions are seamlessly handled via strict idempotency keys, and asynchronous evaluation failures explicitly persist their exception states to the database rather than disappearing silently into standard out.

---

## Quick Start

Get the system running in 4 commands:

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env: Add your LLM_API_KEY and INGEST_API_KEY

# 2. Sync the project environment
uv sync --dev

# 3. Initialize everything (DB + migrations + web container)
just init

# 4. Run the full demo (agents + evaluations + verification)
just demo
```

Then open http://localhost:8000/runs/ to see your telemetry.

If you want Django admin access, create a superuser explicitly:

```bash
uv run python manage.py createsuperuser
```

### Daily Development Workflow

```bash
just sync      # Sync the uv-managed virtualenv
just start     # Start the stack
just demo      # Generate sample telemetry
just test      # Run tests inside the project virtualenv
just logs      # Watch what's happening
just stop      # Shutdown when done
```

### Local Python Workflow

Use the project virtualenv managed by `uv` for all Python commands:

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
```

`pytest -q` without `uv run` can execute against a different interpreter than the synced project environment, which is why the supported workflow is `uv run ...` or the `just` recipes.

### Available Recipes

Run `just --list` to see all 20+ available recipes:

| Recipe | Purpose |
|--------|---------|
| `just init` | First-time setup (DB → migrations → web) |
| `just start` | Start the full application stack |
| `just stop` | Stop all containers cleanly |
| `just demo` | Complete demo cycle (agents + evals + verify) |
| `just agent` | Run research_analyst agent (live tools + LLM) |
| `just raw-agent` | Run raw_sdk_briefing_agent (rule-based) |
| `just status` | Health check for containers + app |
| `just verify` | Verify data integrity |
| `just test` | Run pytest test suite |
| `just logs` | Stream container logs |


## Configuration

The application validates its environment on startup and fails fast if required settings are missing or invalid.

Required variables:
* `DJANGO_SECRET_KEY`: Cryptographic signing key.
* `INGEST_API_KEY`: Authentic key required for span ingestion.
* `DATABASE_URL`: PostgreSQL connection string.

Use non-placeholder values in your local `.env` (the app rejects `REPLACE_ME...` and `changeme...` values for required secrets).

Optional overrides:
* `DEBUG`: Defaults to `False`. When `False`, `ALLOWED_HOSTS` must be explicitly provided.
* `ALLOWED_HOSTS`: Comma-separated list of permitted hostnames.
* `EVAL_LLM_PROVIDER`: Evaluator model provider (defaults to `openai`).
* `LLM_API_KEY`: Required only if the chosen evaluation provider needs one.

### Security Notes for Public Repos

* Keep `.env` local-only (never commit real keys).
* Docker ports are bound to localhost by default for safer local demos.
* Console telemetry logging is metadata-only (span identifiers and attribute keys), not full prompt/output payloads.


## Architecture & Data Model

This project optimizes for engineering signal density and iteration speed. I chose a Django monolith with DRF ingestion to enforce schema boundaries at ingress.

* **Data Model:** An OTel inspired telemetry model maps `Run` (trace) and `Span` (step). A single SQL datastore with a JSON field on `Span.attributes` balances relational guarantees (indexes, transactional integrity) with telemetry flexibility (variable tool and LLM payloads).
* **Ingestion Boundary:** DRF serializers validate incoming spans before persistence. They reject malformed payloads before they reach storage (`core/serializers.py`, `core/views.py`).
* **Asynchronous Quality Loop:** The system evaluates completed runs with an LLM judge, stores explainable scores, and persists evaluation lifecycle evidence for success and failure paths (`core/tasks.py`, `core/models.py`).
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
* **Separation of concerns:** Ingestion, finalization, evaluation, and rendering are isolated in distinct modules.
* **Explicit ingestion semantics:** duplicate ingestion is idempotent and completed runs are immutable.
* **Error observability:** Span exceptions are marked as `ERROR`. The exception message is stored in span attributes rather than failing silently.
* **Durable async failure evidence:** evaluation failures are persisted for inspection instead of being silently dropped.

## Failure Modes I Designed For

A telemetry system is only useful if it behaves predictably when things go wrong. This project explicitly handles and tests several failure modes:

| Failure Mode | Behavior | Why It Matters |
|---|---|---|
| Duplicate ingestion request | Idempotency key prevents duplicate span persistence | Protects metrics and run history from replay drift |
| New span after run completion | Rejected with `409` and no run mutation | Makes completion semantics explicit and stable |
| Telemetry transport error from agent | Tracer fails open and agent execution continues | Observability should not break primary workload execution |
| Hook callback failure | Span ingestion still succeeds; hook error is logged | Optional extension points should not compromise ingestion durability |
| Evaluation JSON parse failure | Failure state is persisted on the `Evaluation` record | Background failures remain queryable instead of silently disappearing |
| Evaluation provider exception | Failure evidence, timestamps, and error message are persisted | Makes async debugging operationally tractable |
| Error spans inside a run | Run can still complete and be evaluated | Real systems degrade; they do not require perfect runs to remain observable |

## Verified Behavior

The project includes automated tests to prove the domain logic works.

| Proof Point | Evidence | Why It Matters |
|---|---|---|
| **Token aggregation** | `tests/test_lifecycle.py` asserts `run.total_tokens == 50` after two spans (`10+5` and `20+15`). | Confirms deterministic rollup logic for run-level metrics. It is not a UI-only estimate. |
| **Reproducible costs** | `core/services/finalization.py` computes `total_cost = Decimal(total_tokens) * Decimal("0.000002")`. | Keeps cost estimates transparent and auditable for completed runs. |
| **Nested span reconstruction** | `tests/test_lifecycle.py` validates root to child to grandchild depth. | Proves parent/child telemetry can be reconstructed for root cause analysis. |
| **Persisted quality signals** | `core/tasks.py` writes `Evaluation`, persists evaluation lifecycle state, and denormalizes `Run.eval_score`. | Demonstrates an end-to-end observe and evaluate loop with queryable failure paths. |
| **Recruiter-relevant KPIs** | `core/models.py` defines `total_tokens`, `total_cost`, and `eval_score`. | Shows the system targets performance, cost, and quality triage in one data plane. |

## Verified Operational Signals

The project now verifies not only happy-path behavior, but also lifecycle and failure semantics.

| Proof Point | Evidence | Why It Matters |
|---|---|---|
| **Atomic idempotent ingestion** | `tests/test_ingest_semantics.py` | Prevents duplicate writes under replay/retry conditions |
| **Completion immutability** | `tests/test_ingest_semantics.py` | Locks run semantics after finalization |
| **Stable rollup totals on retry** | `tests/test_ingest_semantics.py`, `tests/test_lifecycle.py` | Prevents token/cost drift |
| **Durable evaluation failures** | `tests/test_evaluation.py`, `tests/test_failure_tolerance.py` | Async failures remain queryable |
| **Evaluation lifecycle metrics** | `tests/test_evaluation_observability.py`, `tests/test_metrics.py` | Background processing is operationally visible |
| **Structured evaluation logs** | `tests/test_evaluation_observability.py` | Supports debugging and postmortem analysis |
| **End-to-end run reconstruction** | `tests/test_lifecycle.py` | Preserves parent/child execution shape for diagnosis |

### Local Verification

Run the test suite to verify ingestion and rollup logic:

```bash
uv run pytest -q
uv run ruff check .
```

Expected proof signals from this suite:
1. Run creation from first span (`status="running"`).
2. Completion on final span (`status="completed"`).
3. Token rollup at `50` for the controlled test fixture.
4. Span tree depth integrity for nested execution paths.

If the app is running locally, you can also inspect operational counters directly:

```bash
curl http://localhost:8000/metrics/
```

## Live Demo Evidence

The demo is designed to produce inspectable evidence, not just a successful console message.

### Demo workflow

```bash
just init
just demo
```

### Example observed output

```text
🎯 LIVE DEMO EXECUTION SUMMARY
===============================
Showing latest 2 completed run(s).

Run 1: raw_sdk_briefing_agent
  trace_id: 81bcd730-9380-4d69-8cbd-a2980772e9e3
  summary: status=completed | spans=5 | latency=184.4ms | tokens=91 | cost=$0.0002 | eval_score=3.50

Run 2: research_analyst
  trace_id: e419408a-ea8a-4a1a-974e-348a63d267e5
  summary: status=completed | spans=6 | latency=10799.5ms | tokens=395 | cost=$0.0008 | eval_score=4.50
```

### Example failure-and-retry evidence from the same run

```text
3. web_search [tool ERROR] duration=<0.1ms | attempt=1/2 | error=simulated search timeout
4. web_search [tool OK] duration=1587.4ms | attempt=2/2
```

That is the core observability claim of the project: the system does not just record final answers — it preserves execution steps, retries, timing, cost, and quality signals in one inspectable backend.

> Add a GIF here showing `/runs/` and the final summary output after `just demo`.

## What Changed To Strengthen Engineering Signal

Recent improvements were intentionally chosen to increase backend rigor without over-engineering the proposal:

- Extracted ingestion and finalization logic from `core/views.py` into `core/services/*`
- Added explicit ingestion semantics tests for idempotency, completion, and hook isolation
- Added durable evaluation lifecycle evidence on `Evaluation`
- Added started/completed/failed counters for async evaluation
- Added structured logs for evaluation lifecycle events
- Preserved the same monolith shape and queueing model while tightening correctness and observability

These changes do not make the system larger. They make its operational behavior more explicit.

## Demo Agents

There are now two small agent entry points that share the same tracer boundary and ingestion path:

```bash
just agent
just raw-agent
```

* `scripts/demo_agent.py` shows a richer research flow with live tools and an LLM call.
* `scripts/raw_sdk_agent.py` is a standalone hand-rolled Python agent that uses the same `sdk.tracer.Tracer` directly and still posts spans to `/api/v1/ingest/span/`.

That side-by-side contrast is the framework-agnostic proof: two different agent styles, one telemetry pipeline.

## Production Next Steps

If this were expanded beyond portfolio scope, the next steps would be:

- replace in-memory rate limiting with a shared backend
- move from SqlHuey to a more elastic queue only when throughput justifies it
- add tenant-aware auth and isolation for multi-user ingestion
- extend metrics and dashboards for queue latency and evaluator health
- add retention and archival policies for long-lived telemetry data

I intentionally did **not** build those here because the current design is optimized for clarity, correctness, and defensible tradeoffs at portfolio scale.
