import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from prometheus_client import start_http_server
from common.kafka import producer, publish_one
from common.telemetry import setup


class Command(BaseCommand):
    def add_arguments(self, p):
        p.add_argument("--once", action="store_true")
        p.add_argument("--metrics-port", type=int, default=9101)

    def handle(self, *args, **o):
        setup()
        start_http_server(o["metrics_port"])
        client = producer()
        running = True

        def stop(*_):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while running:
                close_old_connections()
                found = publish_one(client)
                if o["once"]:
                    break
                if not found:
                    time.sleep(0.5)
        finally:
            client.flush(20)
