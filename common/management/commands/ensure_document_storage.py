import uuid
import requests
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from common.storage import SupabaseStorage


class Command(BaseCommand):
    def handle(self, *args, **options):
        storage = SupabaseStorage()
        storage.ensure_bucket()
        name = "health-checks/" + uuid.uuid4().hex + ".pdf"
        body = b"%PDF-1.4\nClaimFlow synthetic storage check\n%%EOF"
        uploaded = False
        try:
            storage.save(name, ContentFile(body))
            uploaded = True
            anonymous = requests.get(
                storage.origin
                + "/storage/v1/object/public/"
                + storage.bucket
                + "/"
                + name,
                timeout=(5, 10),
                allow_redirects=False,
            )
            if anonymous.status_code not in {400, 401, 403, 404}:
                raise RuntimeError("Private document isolation could not be verified.")
            with storage.open(name) as file:
                if file.read() != body:
                    raise RuntimeError("Storage verification failed.")
        finally:
            if uploaded:
                storage.delete(name)
        self.stdout.write(
            "Private document storage verified: upload, download, delete."
        )
