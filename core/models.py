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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.trace_id)
