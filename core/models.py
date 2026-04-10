import uuid

from django.db import models


class Run(models.Model):
    STATUS_CHOICES = [
        ("running", "running"),
        ("completed", "completed"),
        ("failed", "failed"),
    ]

    trace_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    agent_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    total_tokens = models.IntegerField(default=0)
    total_cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    eval_score = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True
    )

    class Meta:
        indexes = [models.Index(fields=["status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_tokens__gte=0),
                name="run_total_tokens_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_cost__gte=0),
                name="run_total_cost_non_negative",
            ),
        ]

    def __str__(self):
        return str(self.trace_id)


class Span(models.Model):
    SPAN_TYPE_CHOICES = [("llm", "llm"), ("tool", "tool"), ("chain", "chain")]
    STATUS_CODE_CHOICES = [("OK", "OK"), ("ERROR", "ERROR")]

    span_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    trace_id = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="spans")
    parent_span_id = models.UUIDField(null=True, blank=True)
    span_type = models.CharField(max_length=10, choices=SPAN_TYPE_CHOICES)
    name = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status_code = models.CharField(max_length=10, choices=STATUS_CODE_CHOICES)
    attributes = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["trace_id"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gte=models.F("start_time")),
                name="span_end_after_start",
            ),
        ]

    def __str__(self):
        return self.name


class Evaluation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    trace_id = models.OneToOneField(
        Run, on_delete=models.CASCADE, related_name="evaluation"
    )
    correctness_score = models.IntegerField()
    helpfulness_score = models.IntegerField()
    aggregate_score = models.DecimalField(max_digits=3, decimal_places=2)
    reasoning = models.TextField()
    prompt_version = models.CharField(max_length=10, default="v1")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(correctness_score__gte=1, correctness_score__lte=5),
                name="evaluation_correctness_score_range",
            ),
            models.CheckConstraint(
                condition=models.Q(helpfulness_score__gte=1, helpfulness_score__lte=5),
                name="evaluation_helpfulness_score_range",
            ),
        ]

    def __str__(self):
        return str(self.trace_id)


class IdempotencyKey(models.Model):
    key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["created_at"])]

    def is_expired(self):
        from django.utils import timezone

        return self.created_at < timezone.now() - timezone.timedelta(hours=24)
