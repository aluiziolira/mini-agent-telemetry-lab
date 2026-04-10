from rest_framework import serializers


class SpanIngestSerializer(serializers.Serializer):
    span_id = serializers.UUIDField()
    trace_id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    span_type = serializers.ChoiceField(choices=["llm", "tool", "chain"])
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    status_code = serializers.ChoiceField(choices=["OK", "ERROR"])
    parent_span_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    attributes = serializers.JSONField(required=False, default=dict)
    agent_name = serializers.CharField(
        required=False, default="unknown", max_length=100
    )
    is_final = serializers.BooleanField(required=False, default=False)
