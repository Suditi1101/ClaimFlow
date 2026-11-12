import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from claims.models import Claim
from verification.rag import verify_claim
from verification.rag_clients import RagError


class Command(BaseCommand):
    help = "Run RAG for a pending claim. Existing assessments are reused; final decisions are never changed."

    def add_arguments(self, parser):
        parser.add_argument("claim_id", type=uuid.UUID)

    def handle(self, *args, **options):
        if not settings.CLAIM_RAG["enabled"]:
            raise CommandError(
                "Set RAG_ENABLED=true to enable external document processing."
            )
        try:
            result = verify_claim(options["claim_id"])
        except (Claim.DoesNotExist, ValueError) as exc:
            raise CommandError("Unknown claim ID.") from exc
        except RagError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Assessment: {result.status}; code: {result.error_code or 'none'}. Staff decision required."
        )
