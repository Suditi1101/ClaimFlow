"""Bound total file bytes before Django spools an untrusted upload to disk."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class MaxBytesUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.MAX_DOCUMENT_BYTES:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
