from django.core.management.base import BaseCommand
from payments.models import Payment
from payments.services import settle


class Command(BaseCommand):
    def handle(self, *args, **o):
        for pk in (
            Payment.objects.filter(status="pending")
            .values_list("claim_id", flat=True)
            .iterator(chunk_size=100)
        ):
            settle(pk)
