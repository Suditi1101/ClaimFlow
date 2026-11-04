import base64
import json
import logging
import random
import time
from django.conf import settings
from django.db import OperationalError, InterfaceError
from opentelemetry import trace
from opentelemetry.propagate import extract
from common.kafka import send
from common.models import DeadLetter
from common.processing import process
from common.telemetry import EVENTS, DURATION

log = logging.getLogger(__name__)


def handle_message(consumer, dlq, role, msg):
    error = None
    for attempt in range(4):
        try:
            if len(msg.value() or b"") > 1048576:
                raise ValueError("oversized_event")
            event = json.loads(msg.value())
            context = (
                extract({"traceparent": event.get("traceparent", "")})
                if isinstance(event, dict)
                else None
            )
            with (
                DURATION.labels(role).time(),
                trace.get_tracer("claimflow").start_as_current_span(
                    "consume", context=context
                ),
            ):
                process(role, event)
            EVENTS.labels(role, "processed").inc()
            error = None
            break
        except (OperationalError, InterfaceError):
            raise
        except Exception as exc:
            error = type(exc).__name__
            if isinstance(exc, (ValueError, KeyError, TypeError)):
                break
            if attempt < 3:
                time.sleep(2**attempt + random.random())
    if error:
        dead, _ = DeadLetter.objects.get_or_create(
            source_topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
            consumer=role,
            defaults={
                "raw_base64": base64.b64encode(msg.value() or b"").decode(),
                "error_code": error,
            },
        )
        send(
            dlq,
            settings.KAFKA_DLQ,
            str(dead.pk),
            {
                "dead_letter_id": dead.pk,
                "consumer": role,
                "error_code": error,
            },
        )
        EVENTS.labels(role, "dead_letter").inc()
        log.error("event_dead_letter role=%s id=%s code=%s", role, dead.pk, error)
    consumer.commit(message=msg, asynchronous=False)
