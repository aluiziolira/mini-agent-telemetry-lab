set dotenv-load := true

# =============================================================================
# MINI AGENT TELEMETRY LAB - DEVELOPER WORKFLOWS
# =============================================================================
# 
# QUICK START (First Time):
#   just init
#   just demo
#
# DAILY DEVELOPMENT:
#   just start     # Start the stack
#   just demo      # Run sample agents
#   just logs      # Watch what's happening
#   just stop      # Shutdown when done
#
# =============================================================================


# =============================================================================
# 🚀 SETUP & BOOTSTRAP (Run These First)
# =============================================================================

# Full first-time setup: DB → Migrations → Superuser → Web Container
init:
    @echo "🚀 Initializing Mini Agent Telemetry Lab..."
    docker compose up db -d --wait
    just db-setup
    docker compose up web -d --wait
    @echo ""
    @echo "✅ Setup complete! Access your dashboard at:"
    @echo "   🌐 http://localhost:8000/runs/"
    @echo "   🔐 Admin: http://localhost:8000/admin/ (admin/admin)"
    @echo ""
    @echo "💡 Next: Run 'just demo' to generate sample telemetry"

# Database migrations and superuser creation (requires DB running)
db-setup:
    @echo "📦 Running migrations..."
    uv run python manage.py migrate
    @echo "👤 Creating superuser (admin/admin)..."
    DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_EMAIL=admin@example.com DJANGO_SUPERUSER_PASSWORD=admin \
        uv run python manage.py createsuperuser --noinput 2>/dev/null || echo "   Superuser already exists"


# =============================================================================
# 🏃 DAILY DEVELOPMENT (Your Main Loop)
# =============================================================================

# Start the full application stack (DB + Web)
start:
    @echo "🏃 Starting application stack..."
    docker compose up -d --wait
    @echo ""
    @echo "✅ Stack is running!"
    @echo "   Dashboard: http://localhost:8000/runs/"
    just status

# Stop all containers cleanly
stop:
    @echo "🛑 Stopping containers..."
    docker compose down
    @echo "✅ Stopped"

# Restart everything fresh (useful after code changes or issues)
restart:
    @echo "🔄 Restarting stack..."
    docker compose down
    docker compose up -d --wait
    @echo "✅ Restarted"
    just status

# Quick health check of all services
status:
    @echo ""
    @echo "📊 Container Status:"
    @echo "==================="
    docker compose ps
    @echo ""
    @echo "🔍 Application Health:"
    @echo "====================="
    @curl -s http://localhost:8000/health/ 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"   Status: {d['status']}\"); [print(f\"   - {k}: {v}\") for k,v in d['checks'].items()]" 2>/dev/null || echo "   ⚠️  Health check failed - is the stack running?"
    @echo ""


# =============================================================================
# 🎯 DEMO & TESTING (Generate Telemetry Data)
# =============================================================================

# Run complete demonstration: both agents + evaluations + verification
demo:
    @echo ""
    @echo "🎬 RUNNING COMPLETE DEMO WORKFLOW"
    @echo "=================================="
    @echo ""
    @echo "Step 1/4: Running research_analyst agent (with live tools)..."
    just agent
    @echo ""
    @echo "Step 2/4: Running raw_sdk_briefing_agent (rule-based)..."
    just raw-agent
    @echo ""
    @echo "Step 3/4: Processing evaluations via Huey worker..."
    just eval-pipeline
    @echo ""
    @echo "Step 4/5: Verifying data integrity..."
    just verify
    @echo ""
    @echo "Step 5/5: Surfacing live execution metrics..."
    just demo-summary
    @echo ""
    @echo "✅ Demo complete! View results at:"
    @echo "   http://localhost:8000/runs/"

# Run research_analyst agent (real LLM + stock data + web search)
agent:
    uv run python demo_agent.py "Should I buy AAPL?"

# Run raw_sdk_briefing_agent (rule-based, no external APIs)
raw-agent:
    uv run python raw_sdk_agent.py "Show how a raw Python agent can share the telemetry tracer."


# =============================================================================
# 📊 DATA OPERATIONS (Process & Inspect Telemetry)
# =============================================================================

# Enqueue and process all pending evaluations (runs worker with timeout)
eval-pipeline:
    @echo "📋 Enqueueing evaluations..."
    uv run python manage.py eval_pending
    @echo "⚙️  Processing evaluations (timeout: 30s)..."
    timeout 30 uv run python manage.py run_huey 2>&1 | grep -E "(Enqueued|Executing|completed|eval_score)" || true
    @echo "✅ Evaluations complete"

# Run interactive Huey worker (blocks - use Ctrl+C to stop)
worker:
    @echo "⚙️  Starting Huey worker (Press Ctrl+C to stop)..."
    uv run python manage.py run_huey

# Verify data integrity (orphaned spans, negative values, etc.)
verify:
    @echo "🔍 Verifying data integrity..."
    uv run python manage.py verify_data

# Summarize recent completed runs with recruiter-friendly execution evidence
demo-summary:
    @echo "🎯 Summarizing the latest demo runs..."
    uv run python manage.py demo_summary --limit 2

# Surface the strongest README proof points through focused tests
proof:
    @echo "📌 Portfolio proof points from the test suite:"
    @echo "   - run lifecycle transitions from running to completed"
    @echo "   - completed runs roll up 150 tokens and Decimal(\"0.0003\") cost"
    @echo "   - nested spans reconstruct root -> child -> grandchild"
    @echo "   - evaluations persist and denormalize run.eval_score == Decimal(\"4.5\")"
    @echo "   - metrics expose spans_ingested_total and eval_tasks_completed_total"
    @echo ""
    uv run pytest -q -vv \
        tests/test_lifecycle.py::test_full_run_lifecycle_rolls_up_metrics_and_links_spans \
        tests/test_lifecycle.py::test_build_span_tree_reconstructs_nested_trace_for_run_detail \
        tests/test_evaluation.py::test_completed_run_is_scored_and_denormalized_for_review \
        tests/test_metrics.py::test_metrics_endpoint_persists_span_ingestion_count \
        tests/test_metrics.py::test_metrics_endpoint_reflects_completed_evaluations

# Show Prometheus metrics endpoint
metrics:
    @echo "📈 Prometheus Metrics:"
    @echo "===================="
    @echo ""
    @curl -s http://localhost:8000/metrics/


# Run test suite
test:
    @echo "🧪 Running tests..."
    uv run pytest -q


# =============================================================================
# 🔧 DEVELOPER UTILITIES (Debugging & Monitoring)
# =============================================================================

# Follow container logs in real-time (Ctrl+C to exit)
logs:
    @echo "📜 Streaming logs (Ctrl+C to exit)..."
    docker compose logs -f

# Rebuild web container image (use after code changes)
rebuild:
    @echo "🔨 Rebuilding web container..."
    docker compose build web
    docker compose up web -d
    @echo "✅ Rebuilt and restarted"

# Quick preview of recent runs (no browser needed)
runs:
    @echo "📊 Recent Runs:"
    @echo "=============="
    @curl -s http://localhost:8000/runs/ 2>/dev/null | \
        grep -oP '(?<=>)[^<]+(?=</td>)' | \
        head -24 | \
        paste -d " " - - - - - - 2>/dev/null | \
        column -t -s " " 2>/dev/null || \
        echo "   Visit: http://localhost:8000/runs/"

# Open dashboard in browser (Linux/Mac)
open:
    @echo "🌐 Opening dashboard..."
    @python3 -m webbrowser http://localhost:8000/runs/ 2>/dev/null || \
    xdg-open http://localhost:8000/runs/ 2>/dev/null || \
    open http://localhost:8000/runs/ 2>/dev/null || \
    echo "   Open manually: http://localhost:8000/runs/"

# Clean up everything (containers, volumes, images)
clean:
    @echo "🧹 Cleaning up..."
    docker compose down -v --rmi local 2>/dev/null || true
    @echo "✅ Cleaned"


# =============================================================================
# 📚 REFERENCE
# =============================================================================
#
# QUICK REFERENCE:
#   just init         # First-time setup
#   just start        # Start the stack
#   just demo         # Run full demo
#   just logs         # Watch logs
#   just stop         # Shutdown
#
# URLS:
#   http://localhost:8000/runs/      # Dashboard
#   http://localhost:8000/admin/      # Django Admin (admin/admin)
#   http://localhost:8000/health/     # Health check
#   http://localhost:8000/metrics/    # Prometheus metrics
#
# =============================================================================
