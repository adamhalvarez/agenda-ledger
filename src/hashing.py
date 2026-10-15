"""Content hashing. sha256 of raw bytes, never Python's salted hash().

Ported from the same rule in record-scanner/scripts/fetch.py: cache filenames
and dedup keys must be deterministic across processes and across days.
"""

import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
