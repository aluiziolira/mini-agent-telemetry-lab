"""Database-backed metrics counters for observability."""

from django.db import IntegrityError
from django.db.models import F

from core.models import MetricCounter


METRIC_DESCRIPTIONS = {
    "spans_ingested_total": "Total number of spans ingested",
    "eval_tasks_completed_total": "Total number of evaluation tasks completed",
}


class Metrics:
    """Database-backed metrics store."""

    def _ensure_counter_exists(self, metric_name):
        try:
            MetricCounter.objects.get_or_create(name=metric_name)
        except IntegrityError:
            pass

    def _increment(self, metric_name):
        self._ensure_counter_exists(metric_name)
        MetricCounter.objects.filter(name=metric_name).update(value=F("value") + 1)

    def _get_metric_values(self):
        for metric_name in METRIC_DESCRIPTIONS:
            self._ensure_counter_exists(metric_name)

        stored_values = dict(
            MetricCounter.objects.filter(name__in=METRIC_DESCRIPTIONS).values_list(
                "name", "value"
            )
        )
        return {
            metric_name: stored_values.get(metric_name, 0)
            for metric_name in METRIC_DESCRIPTIONS
        }

    def increment_spans_ingested(self):
        self._increment("spans_ingested_total")

    def increment_eval_tasks_completed(self):
        self._increment("eval_tasks_completed_total")

    def get_prometheus_text(self):
        metric_values = self._get_metric_values()
        lines = []
        for metric_name, description in METRIC_DESCRIPTIONS.items():
            lines.extend(
                [
                    f"# HELP {metric_name} {description}",
                    f"# TYPE {metric_name} counter",
                    f"{metric_name} {metric_values[metric_name]}",
                    "",
                ]
            )
        return "\n".join(lines)


metrics = Metrics()
