"""
Where documents are kept: the S3 bucket from .env (AWS_STORAGE_BUCKET_NAME ...), or this server's disk
(MEDIA_ROOT/documents) while no bucket is set. Files are private either way: the app serves them after checking who
is asking (reports.document_views), never through a public URL.

DocumentStorage picks the backend each time it's used rather than once at start-up, so the bucket can be switched
on by editing .env and restarting, and tests can point it at a fake bucket.
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

_backends = {}


def _backend():
    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
    if bucket:
        key = ("s3", bucket, settings.AWS_S3_REGION_NAME, settings.AWS_S3_ENDPOINT_URL, settings.AWS_DOCUMENTS_PREFIX,
               settings.AWS_ACCESS_KEY_ID)
        if key not in _backends:
            from storages.backends.s3 import S3Storage

            _backends[key] = S3Storage(
                bucket_name=bucket,
                access_key=settings.AWS_ACCESS_KEY_ID or None,
                secret_key=settings.AWS_SECRET_ACCESS_KEY or None,
                region_name=settings.AWS_S3_REGION_NAME or None,
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                location=settings.AWS_DOCUMENTS_PREFIX,
                default_acl="private",
                file_overwrite=False,
                querystring_auth=True,
                object_parameters={"ServerSideEncryption": "AES256"},
            )
        return _backends[key]
    location = os.path.join(settings.MEDIA_ROOT, "documents")
    key = ("disk", location)
    if key not in _backends:
        _backends[key] = FileSystemStorage(location=location)
    return _backends[key]


def storage_name():
    """'S3 bucket <name>' or 'server disk' — where new documents are going."""
    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
    return f"S3 bucket {bucket}" if bucket else "server disk"


@deconstructible
class DocumentStorage(Storage):
    """Storage for LEMDocument files: S3 when a bucket is configured, else the server's disk."""

    def _open(self, name, mode="rb"):
        return _backend().open(name, mode)

    def _save(self, name, content):
        return _backend().save(name, content)

    def get_available_name(self, name, max_length=None):
        return _backend().get_available_name(name, max_length=max_length)

    def generate_filename(self, filename):
        return _backend().generate_filename(filename)

    def delete(self, name):
        return _backend().delete(name)

    def exists(self, name):
        return _backend().exists(name)

    def size(self, name):
        return _backend().size(name)

    def url(self, name):
        return _backend().url(name)

    def __eq__(self, other):
        return isinstance(other, DocumentStorage)

    def __hash__(self):
        return hash(DocumentStorage)


document_storage = DocumentStorage()
