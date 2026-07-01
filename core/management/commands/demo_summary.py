from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Run


def _duration_ms(start_time, end_time):
    if not start_time or not end_time:
        return None
    return (end_time - start_time).total_seconds() * 1000


def _format_ms(value):
    if value is None:
        return "n/a"
    if value == 0:
        return "0ms"
    if value < 0.1:
        return "<0.1ms"
    return f"{value:.1f}ms"


class Command(BaseCommand):
    help = "Show a high-signal summary of recent completed demo runs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=2,
            help="Number of recent completed runs to summarize",
        )

    def handle(self, *args, **options):
        limit = max(options["limit"], 1)
        runs = list(
            Run.objects.filter(status="completed")
            .prefetch_related("spans", "evaluation")
            .order_by("-start_time")[:limit]
        )

        if not runs:
            self.stdout.write("No completed runs found. Run 'just demo' first.")
            return

        self.stdout.write("🎯 LIVE DEMO EXECUTION SUMMARY")
        self.stdout.write("===============================")
        self.stdout.write(f"Showing latest {len(runs)} completed run(s).")
        self.stdout.write("")

        for index, run in enumerate(runs, start=1):
            spans = sorted(run.spans.all(), key=lambda span: span.start_time)  # type: ignore[attr-defined]
            total_latency_ms = _duration_ms(run.start_time, run.end_time)
            eval_score = run.eval_score if run.eval_score is not None else "pending"

            self.stdout.write(f"Run {index}: {run.agent_name}")
            self.stdout.write(f"  trace_id: {run.trace_id}")
            summary = (
                f"status={run.status} | spans={len(spans)} | "
                f"latency={_format_ms(total_latency_ms)} | tokens={run.total_tokens} | "
                f"cost=${Decimal(run.total_cost):.4f} | eval_score={eval_score}"
            )
            self.stdout.write(f"  summary: {summary}")
            self.stdout.write("  steps:")

            for step_index, span in enumerate(spans, start=1):
                span_duration_ms = _duration_ms(span.start_time, span.end_time)
                prompt_tokens = span.attributes.get("prompt_tokens", 0)
                completion_tokens = span.attributes.get("completion_tokens", 0)
                span_tokens = prompt_tokens + completion_tokens
                attempt = span.attributes.get("attempt")
                max_attempts = span.attributes.get("max_attempts")
                synthetic_completion = span.name == "run_finish" and span_duration_ms == 0
                display_duration = (
                    "synthetic" if synthetic_completion else _format_ms(span_duration_ms)
                )
                status_label = "OK" if span.status_code == "OK" else "ERROR"

                line = (
                    f"    {step_index}. {span.name} "
                    f"[{span.span_type} {status_label}] "
                    f"duration={display_duration}"
                )
                if attempt and max_attempts:
                    line += f" | attempt={attempt}/{max_attempts}"
                if span_tokens:
                    line += f" | tokens={span_tokens}"
                if span.status_code == "ERROR":
                    error_message = span.attributes.get("error_message")
                    if error_message:
                        line += f" | error={error_message}"
                if synthetic_completion:
                    line += " | completion_marker=true"

                self.stdout.write(line)

            self.stdout.write("")
