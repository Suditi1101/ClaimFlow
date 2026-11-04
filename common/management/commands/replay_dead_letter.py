import base64
import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from common.models import DeadLetter
from common.processing import process


class Command(BaseCommand):
    help = "Operator replay into original consumer, retaining event ID. Restrict shell access."

    def add_arguments(self, p):
        p.add_argument("id", type=int)

    def handle(self, *args, **o):
        dead = DeadLetter.objects.get(pk=o["id"])
        if dead.replayed_at:
            raise CommandError("Already replayed")
        process(dead.consumer, json.loads(base64.b64decode(dead.raw_base64)))
        dead.replayed_at = timezone.now()
        dead.save(update_fields=["replayed_at"])
        self.stdout.write("Replay processed")
