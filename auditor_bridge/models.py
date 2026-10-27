import uuid

from django.db import models


class RepositoryLink(models.Model):
    identity_key = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=16)
    instance = models.CharField(max_length=512)
    repository_id = models.CharField(max_length=128)
    inventory = models.JSONField(default=dict)
    product = models.OneToOneField("dojo.Product", on_delete=models.PROTECT)
    engagement = models.OneToOneField("dojo.Engagement", on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class ScanImport(models.Model):
    scan_id = models.UUIDField(primary_key=True)
    repository = models.ForeignKey(RepositoryLink, on_delete=models.PROTECT)
    test = models.OneToOneField("dojo.Test", on_delete=models.PROTECT)
    report_digest = models.CharField(max_length=64)
    commit_sha = models.CharField(max_length=64, default="")
    finding_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class CallbackReceipt(models.Model):
    callback_id = models.UUIDField(primary_key=True)
    payload_digest = models.CharField(max_length=64)
    acknowledgement = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class Profile(models.Model):
    key = models.SlugField(max_length=100, unique=True)
    sync_key = models.UUIDField(default=uuid.uuid4, unique=True)
    desired = models.JSONField()
    # Retain the original POST body until its idempotent result is recovered.
    creation_payload = models.JSONField()
    sequence_id = models.UUIDField(null=True)
    state = models.CharField(max_length=16, default="pending")
    last_error = models.CharField(max_length=80, blank=True)
    remote = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class InventoryState(models.Model):
    key = models.CharField(max_length=20, primary_key=True, default="default")
    watermark = models.DateTimeField(null=True)
    last_success = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=80, blank=True)
