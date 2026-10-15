import hashlib

from src.hashing import sha256_bytes


def test_matches_stdlib_hashlib():
    data = b"agenda item 12: approve contract"
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_deterministic_across_calls():
    data = b"same bytes twice"
    assert sha256_bytes(data) == sha256_bytes(data)


def test_different_bytes_different_hash():
    assert sha256_bytes(b"version one") != sha256_bytes(b"version two")
