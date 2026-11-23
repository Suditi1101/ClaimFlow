from datetime import date
from django.core.management.base import BaseCommand, CommandError
from policies.models import Policy
from verification.rag import ingest_policy
from verification.rag_clients import RagError


class Command(BaseCommand):
    help = "Index an operator-approved policy wording PDF; never use customer evidence as policy knowledge."

    def add_arguments(self, parser):
        parser.add_argument("--policy-reference", required=True)
        parser.add_argument("--pdf", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--title", required=True)
        parser.add_argument("--effective-from", required=True, type=date.fromisoformat)
        parser.add_argument("--effective-to", required=True, type=date.fromisoformat)

    def handle(self, *args, **options):
        try:
            policy = Policy.objects.get(reference=options["policy_reference"])
            with open(options["pdf"], "rb") as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
            knowledge = ingest_policy(
                policy,
                data,
                options["version"],
                options["title"],
                options["effective_from"],
                options["effective_to"],
            )
        except Policy.DoesNotExist as exc:
            raise CommandError("Unknown policy reference.") from exc
        except OSError as exc:
            raise CommandError("Cannot read policy PDF.") from exc
        except RagError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Policy knowledge ready: {knowledge.pk}"))
