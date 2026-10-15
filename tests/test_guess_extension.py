"""Regression coverage for the PrimeGov mislabeled-content-type bug
(verified live 2026-08-30): sniffed bytes must win over a declared
Content-Type, since a real pilot source has been proven to lie about it.
"""

from pathlib import Path

from src.scan import guess_extension

FIXTURES = Path(__file__).parent / "fixtures" / "text_extract"


def test_sniffs_pdf_even_when_content_type_says_json():
    data = (FIXTURES / "pdf_mislabeled_as_json.pdf").read_bytes()
    ext = guess_extension(data, "application/json; charset=utf-8", "https://example.com/?historyId=abc")
    assert ext == "pdf"


def test_trusts_declared_content_type_when_bytes_dont_sniff():
    ext = guess_extension(b"not a real file", "application/pdf", "https://example.com/doc")
    assert ext == "pdf"


def test_falls_back_to_url_suffix_when_nothing_else_is_known():
    ext = guess_extension(b"plain bytes", "", "https://example.com/file.txt")
    assert ext == "txt"


def test_falls_back_to_bin_when_nothing_is_known_at_all():
    ext = guess_extension(b"plain bytes", "", "https://example.com/no-extension")
    assert ext == "bin"
