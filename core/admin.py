from django.contrib import admin

from core.models import Evaluation, Run, Span


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = (
        "trace_id",
        "agent_name",
        "status",
        "start_time",
        "total_tokens",
        "eval_score",
    )


@admin.register(Span)
class SpanAdmin(admin.ModelAdmin):
    list_display = (
        "span_id",
        "trace_id",
        "name",
        "span_type",
        "status_code",
        "start_time",
    )


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "trace_id",
        "aggregate_score",
        "correctness_score",
        "helpfulness_score",
        "created_at",
    )
