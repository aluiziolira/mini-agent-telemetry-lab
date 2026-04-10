"""In-memory metrics counters for observability."""

from threading import Lock


class Metrics:
    """Thread-safe in-memory metrics store."""

    def __init__(self):
        self._lock = Lock()
        self.spans_ingested_total = 0
        self.eval_tasks_completed_total = 0

    def increment_spans_ingested(self):
        with self._lock:
            self.spans_ingested_total += 1

    def increment_eval_tasks_completed(self):
        with self._lock:
            self.eval_tasks_completed_total += 1

    def get_prometheus_text(self):
        with self._lock:
            return f"""# HELP spans_ingested_total Total number of spans ingested
# TYPE spans_ingested_total counter
spans_ingested_total {self.spans_ingested_total}

# HELP eval_tasks_completed_total Total number of evaluation tasks completed
# TYPE eval_tasks_completed_total counter
eval_tasks_completed_total {self.eval_tasks_completed_total}
"""


metrics = Metrics()
