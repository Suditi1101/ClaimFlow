import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from policies.documents import index_next_document


class Command(BaseCommand):
    help = "Process uploaded policy documents using the RAG ingestion pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Process one pending document and exit."
        )

    def handle(self, *args, **options):
        running = True

        def stop(*_):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while running:
            close_old_connections()
            worked = index_next_document()
            if options["once"]:
                self.stdout.write(
                    "Processed one policy document."
                    if worked
                    else "No policy documents pending."
                )
                return
            if not worked:
                time.sleep(2)
