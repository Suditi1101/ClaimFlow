from common.consumer import handle_message
import logging
import signal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from confluent_kafka import Consumer
from prometheus_client import start_http_server
from common.kafka import producer
from common.processing import ROLES
from common.telemetry import setup

log = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, p):
        p.add_argument("role", choices=ROLES)
        p.add_argument("--metrics-port", type=int, default=9102)

    def handle(self, *args, **o):
        setup()
        start_http_server(o["metrics_port"])
        role = o["role"]
        running = True

        def stop(*_):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        consumer = Consumer(
            {
                **settings.KAFKA,
                "group.id": f"claimflow-{role}-v1",
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "auto.offset.reset": "earliest",
                "max.poll.interval.ms": 300000,
                "fetch.message.max.bytes": 1048576,
            }
        )
        dlq = producer()
        consumer.subscribe([settings.KAFKA_TOPIC])
        try:
            while running:
                msg = consumer.poll(1)
                if msg is None:
                    continue
                if msg.error():
                    raise RuntimeError("kafka_consume_failed")
                close_old_connections()
                handle_message(consumer, dlq, role, msg)
        finally:
            consumer.close()
            dlq.flush(20)
