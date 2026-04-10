from django.core.management.base import BaseCommand

from core.models import Run
from core.tasks import evaluate_run


class Command(BaseCommand):
    help = "Enqueue evaluation tasks for completed unevaluated runs"

    def handle(self, *args, **options):
        pending = Run.objects.filter(status="completed", eval_score__isnull=True)
        count = 0
        for run in pending:
            evaluate_run(str(run.trace_id))
            count += 1
        self.stdout.write(f"Enqueued {count} evaluation task(s).")
