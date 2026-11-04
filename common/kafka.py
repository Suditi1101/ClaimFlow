import json
import random
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from confluent_kafka import Producer, KafkaException
from common.models import Outbox
from common.events import envelope
from common.telemetry import PUBLISH


def producer():
    return Producer(
        {
            **settings.KAFKA,
            "enable.idempotence": True,
            "acks": "all",
            "delivery.timeout.ms": 15000,
            "request.timeout.ms": 5000,
            "message.max.bytes": 1048576,
        }
    )


def send(client, topic, key, value):
    delivered = []
    client.produce(
        topic,
        key=str(key).encode(),
        value=json.dumps(value).encode(),
        on_delivery=lambda err, msg: delivered.append(err),
    )
    remaining = client.flush(20)
    if remaining or not delivered or delivered[0] is not None:
        raise RuntimeError("kafka_delivery_failed")


def publish_one(client):
    # Bounded broker call while row locked; duplicate delivery after crash is expected.
    with transaction.atomic():
        row = (
            Outbox.objects.select_for_update(skip_locked=True)
            .filter(published_at=None, available_at__lte=timezone.now())
            .order_by("created_at")
            .first()
        )
        if row is None:
            return False
        try:
            send(client, settings.KAFKA_TOPIC, row.claim_id, envelope(row))
        except (RuntimeError, BufferError, KafkaException):
            row.attempts += 1
            row.available_at = timezone.now() + timedelta(
                seconds=min(300, 2 ** min(row.attempts, 8)) + random.random()
            )
            row.save(update_fields=["attempts", "available_at"])
            PUBLISH.labels("retry").inc()
        else:
            row.published_at = timezone.now()
            row.save(update_fields=["published_at"])
            PUBLISH.labels("published").inc()
        return True
