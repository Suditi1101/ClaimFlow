"""Private Supabase Storage adapter; credentials never reach the browser."""

import io
from urllib.parse import quote, urlsplit
import requests
from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


class StorageUnavailable(OSError):
    pass


@deconstructible
class SupabaseStorage(Storage):
    def __init__(self):
        self.origin = settings.SUPABASE_STORAGE_URL.rstrip("/")
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".supabase.co")
            or parsed.path not in {"", "/"}
        ):
            raise StorageUnavailable("Invalid storage configuration.")
        self.bucket = settings.SUPABASE_STORAGE_BUCKET
        self.key = settings.SUPABASE_STORAGE_KEY
        if not self.key:
            raise StorageUnavailable("Storage credentials are missing.")

    def request(self, method, path, **kwargs):
        headers = {
            "apikey": self.key,
            "Authorization": "Bearer " + self.key,
            **kwargs.pop("headers", {}),
        }
        try:
            return requests.request(
                method,
                self.origin + "/storage/v1/" + path,
                headers=headers,
                timeout=(5, 20),
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            raise StorageUnavailable(
                "Document storage is temporarily unavailable."
            ) from None

    def object_path(self, name):
        if name.startswith("/") or ".." in name.split("/") or chr(92) in name:
            raise StorageUnavailable("Invalid document path.")
        return "object/" + quote(self.bucket, safe="") + "/" + quote(name, safe="/")

    def require(self, response, allowed=(200, 201)):
        if response.status_code not in allowed:
            raise StorageUnavailable("Document storage is temporarily unavailable.")
        return response

    def _save(self, name, content):
        data = content.read(settings.MAX_DOCUMENT_BYTES + 1)
        if len(data) > settings.MAX_DOCUMENT_BYTES:
            raise StorageUnavailable("Document exceeds upload limit.")
        self.require(
            self.request(
                "POST",
                self.object_path(name),
                data=data,
                headers={"Content-Type": "application/pdf", "x-upsert": "false"},
            )
        )
        return name

    def _open(self, name, mode="rb"):
        if mode not in {"r", "rb"}:
            raise ValueError("Read-only document access.")
        response = self.require(self.request("GET", self.object_path(name)))
        return File(io.BytesIO(response.content), name=name)

    def exists(self, name):
        response = self.request("HEAD", self.object_path(name))
        if response.status_code in {400, 404}:
            return False
        self.require(response)
        return True

    def delete(self, name):
        self.require(self.request("DELETE", self.object_path(name)), (200, 204, 404))

    def size(self, name):
        return int(
            self.require(self.request("HEAD", self.object_path(name))).headers[
                "Content-Length"
            ]
        )

    def url(self, name):
        raise NotImplementedError("Use the authenticated document download endpoint.")

    def ensure_bucket(self):
        response = self.request("GET", "bucket/" + quote(self.bucket, safe=""))
        if response.status_code == 200:
            if response.json().get("public") is not False:
                raise StorageUnavailable("Document bucket must be private.")
            return
        if response.status_code not in {400, 404}:
            self.require(response)
        self.require(
            self.request(
                "POST",
                "bucket",
                json={
                    "id": self.bucket,
                    "name": self.bucket,
                    "public": False,
                    "file_size_limit": settings.MAX_DOCUMENT_BYTES,
                    "allowed_mime_types": ["application/pdf"],
                },
            )
        )
