from rest_framework import serializers

EVENTS = ["push", "merge_request.opened", "merge_request.updated", "merge_request.merged", "tag.created",
          "pipeline.succeeded", "release.published", "deployment.succeeded", "repository.discovered"]


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            msg = "Unknown fields or invalid object"
            raise serializers.ValidationError({"non_field_errors": [msg]})
        return super().to_internal_value(data)


class StepSerializer(StrictSerializer):
    job_slug = serializers.RegexField(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class RepositoryBindingSerializer(StrictSerializer):
    product_id = serializers.IntegerField(min_value=1)
    expected_product_id = serializers.IntegerField(min_value=1)


class ScheduleSerializer(StrictSerializer):
    kind = serializers.ChoiceField(["interval", "weekly"])
    interval_seconds = serializers.IntegerField(min_value=60, max_value=31536000, required=False)
    weekdays = serializers.ListField(child=serializers.IntegerField(min_value=0, max_value=6), min_length=1, max_length=7, required=False)
    hour = serializers.IntegerField(min_value=0, max_value=23, required=False)
    minute = serializers.IntegerField(min_value=0, max_value=59, required=False)
    timezone = serializers.CharField(max_length=100, required=False)

    def validate(self, attrs):
        expected = {"kind", "interval_seconds"} if attrs["kind"] == "interval" else {"kind", "weekdays", "hour", "minute", "timezone"}
        if set(attrs) != expected:
            msg = "Schedule fields do not match kind"
            raise serializers.ValidationError(msg)
        if attrs["kind"] == "weekly":
            attrs["weekdays"] = sorted(set(attrs["weekdays"]))
        return attrs


class AutomationSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    integration_id = serializers.UUIDField()
    repository_ids = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=2**63 - 1), max_length=100, default=list)
    events = serializers.ListField(child=serializers.ChoiceField(EVENTS), max_length=9, default=list)
    ref_patterns = serializers.ListField(child=serializers.CharField(max_length=255), min_length=1, max_length=20, default=lambda: ["@default"])
    schedule = ScheduleSerializer(required=False, allow_null=True)
    max_scans_per_hour = serializers.IntegerField(min_value=1, max_value=240, default=6)
    enabled = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if not attrs["events"] and not attrs.get("schedule"):
            msg = "An event or schedule is required"
            raise serializers.ValidationError(msg)
        attrs["repository_ids"] = sorted(set(attrs["repository_ids"]))
        attrs["events"] = list(dict.fromkeys(attrs["events"]))
        attrs["ref_patterns"] = list(dict.fromkeys(attrs["ref_patterns"]))
        return attrs


class ProfileSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    steps = StepSerializer(many=True, min_length=1, max_length=50)
    asoc_integration_ids = serializers.ListField(child=serializers.UUIDField(), max_length=20)
    automations = AutomationSerializer(many=True, max_length=20, default=list)
    sg_blocking = serializers.BooleanField(default=False)

    def validate_asoc_integration_ids(self, value):
        return list(dict.fromkeys(value))


class CallbackSerializer(StrictSerializer):
    callback_id = serializers.UUIDField()
    scan_id = serializers.UUIDField()
    scan_status = serializers.ChoiceField(["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"])
    provider = serializers.ChoiceField(["gitlab"])
    instance = serializers.CharField(max_length=512)
    repository_id = serializers.RegexField(r"^[1-9][0-9]{0,19}$")
    commit_sha = serializers.RegexField(r"^[a-fA-F0-9]{40,64}$", allow_null=True, required=False)
    report_url = serializers.CharField(max_length=2048)
    context = serializers.DictField(default=dict)
