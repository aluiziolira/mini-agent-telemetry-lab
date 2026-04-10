"""Management command to verify data integrity.

Checks for orphaned spans, runs without spans, and negative values.
"""

from django.core.management.base import BaseCommand

from core.models import Run, Span


class Command(BaseCommand):
    help = "Check data consistency and report issues"

    def handle(self, *args, **options):
        issues = []

        spans = Span.objects.all()
        trace_spans = {}
        for span in spans:
            if span.trace_id_id not in trace_spans:
                trace_spans[span.trace_id_id] = set()
            trace_spans[span.trace_id_id].add(str(span.span_id))

        for span in spans:
            if span.parent_span_id:
                parent_exists = str(span.parent_span_id) in trace_spans.get(span.trace_id_id, set())
                if not parent_exists:
                    issues.append(f"Orphaned span {span.span_id} in trace {span.trace_id_id}")

        runs_with_spans = set(Span.objects.values_list("trace_id", flat=True))
        for run in Run.objects.all():
            if run.trace_id not in runs_with_spans:
                issues.append(f"Run {run.trace_id} has no spans")

        for run in Run.objects.filter(total_tokens__lt=0):
            issues.append(f"Run {run.trace_id} has negative total_tokens")

        for run in Run.objects.filter(total_cost__lt=0):
            issues.append(f"Run {run.trace_id} has negative total_cost")

        if issues:
            self.stdout.write("Data integrity issues found:")
            for issue in issues:
                self.stdout.write(f"  - {issue}")
            self.stdout.write(f"\nTotal issues: {len(issues)}")
        else:
            self.stdout.write("No consistency issues found.")
