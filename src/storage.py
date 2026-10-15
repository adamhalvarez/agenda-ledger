"""Supabase Storage. Every object's path is derived from its content hash,
so re-uploading identical bytes is a safe no-op and the path itself is a
verifiable claim about the content rather than an assigned name.
"""

from .db import get_client

BUCKET = "document-snapshots"


def storage_path(sha256, extension):
    ext = extension.lstrip(".")
    return f"{sha256[:2]}/{sha256}.{ext}" if ext else f"{sha256[:2]}/{sha256}"


def upload(sha256, extension, data: bytes, content_type: str):
    path = storage_path(sha256, extension)
    client = get_client()
    client.storage.from_(BUCKET).upload(
        path,
        data,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return path
