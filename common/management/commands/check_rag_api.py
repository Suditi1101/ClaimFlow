"""Read-only diagnostics; never print API keys or raw document/model payloads."""

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from claims.models import Claim
from verification.models import ClaimAssessment
from verification.rag import generate, assess
from verification.rag_clients import RagError, embed, qdrant
from verification.rag_validation import MESSAGES


class Command(BaseCommand):
    help = "Test Gemini, Qdrant and Groq with synthetic data; optionally diagnose a saved claim without changing it."

    def add_arguments(self, parser):
        parser.add_argument("--claim-id", type=uuid.UUID)
        parser.add_argument(
            "--show-findings",
            action="store_true",
            help="Print validated findings; a real claim can contain private medical information.",
        )

    def handle(self, *args, **options):
        try:
            if options["claim_id"]:
                if not settings.CLAIM_RAG["enabled"]:
                    raise CommandError(
                        "Enable RAG before sending claim evidence to providers."
                    )
                claim = Claim.objects.get(pk=options["claim_id"])
                saved = ClaimAssessment.objects.filter(claim=claim).first()
                if saved and saved.evidence and saved.policy_sources:
                    self.stdout.write(
                        f"Saved context: {len(saved.evidence)} evidence pages; {len(saved.policy_sources)} policy chunks."
                    )
                    result = generate(claim, saved.evidence, saved.policy_sources)
                else:
                    result = assess(claim)
                    if result["error_code"]:
                        raise CommandError(
                            result["summary"] + " Code: " + result["error_code"]
                        )
            else:
                vectors = embed(
                    ["Synthetic hospitalization policy coverage query."],
                    task_type="RETRIEVAL_QUERY",
                )
                self.stdout.write(
                    f"Gemini: PASS; embedding dimensions={len(vectors[0])}."
                )
                qdrant("GET", "")
                self.stdout.write("Qdrant: PASS; configured collection is accessible.")
                claim = SimpleNamespace(
                    pk="synthetic",
                    amount=Decimal("100.00"),
                    incident_date=date(2026, 1, 1),
                    description="Synthetic test: itemized invoice is missing.",
                )
                result = generate(
                    claim,
                    [
                        {
                            "id": "synthetic-document-1",
                            "page": 1,
                            "text": "Synthetic discharge summary. No itemized invoice has been supplied.",
                        }
                    ],
                    [
                        {
                            "id": "synthetic-policy-1",
                            "page": 1,
                            "text": "Synthetic rule: an itemized invoice is required for review.",
                        }
                    ],
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Groq: PASS; structured findings and citation validation passed; findings={len(result['findings'])}."
                )
            )
            if options["show_findings"]:
                self.stdout.write(
                    json.dumps(
                        {"summary": result["summary"], "findings": result["findings"]},
                        indent=2,
                    )
                )
            self.stdout.write(
                "No claim record was changed. Review cited evidence to judge factual correctness."
            )
        except Claim.DoesNotExist as exc:
            raise CommandError("Unknown claim ID.") from exc
        except RagError as exc:
            reason = MESSAGES.get(
                exc.detail, "Check the safe API status logs and configuration."
            )
            raise CommandError(f"{exc}: {reason}") from exc
