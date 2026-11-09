import uuid
from django.db import models
from django.utils import timezone


class Outbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim_id = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    traceparent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["published_at", "available_at"])]


class ProcessedEvent(models.Model):
    event_id = models.UUIDField()
    consumer = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "consumer"], name="unique_consumer_event"
            )
        ]


class DeadLetter(models.Model):
    source_topic = models.CharField(max_length=255)
    partition = models.IntegerField()
    offset = models.BigIntegerField()
    consumer = models.CharField(max_length=40)
    raw_base64 = models.TextField()
    error_code = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    replayed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_topic", "partition", "offset", "consumer"],
                name="unique_failed_delivery",
            )
        ]


class OwnerSetup(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    completed_at = models.DateTimeField(null=True)
