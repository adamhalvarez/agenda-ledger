"""sample_agenda.pdf is a real captured LA Police Commission agenda
(lapdonlinestrgeacc.blob.core.usgovcloudapi.net/lapdonlinemedia/010626.pdf,
fetched 2026-08-31) -- extraction is tested against real output, not a
synthetic PDF.

pdf_mislabeled_as_json.pdf is a real recorded response from PrimeGov's
attachment endpoint (verified live 2026-08-30): a genuine PDF (confirmed by
its %PDF- magic bytes) served with Content-Type: application/json. This is
the regression fixture for that bug -- extract_text must sniff real bytes
rather than trust the declared header.
"""

from pathlib import Path

from src.text_extract import _strip_null_bytes, extract_text, sniff_kind

FIXTURES = Path(__file__).parent / "fixtures" / "text_extract"


def test_extracts_real_text_from_a_real_pdf():
    data = (FIXTURES / "sample_agenda.pdf").read_bytes()
    text = extract_text("application/pdf", data)
    assert text is not None
    assert "LOS ANGELES POLICE COMMISSION" in text


def test_extracts_text_from_html():
    html = b"<html><body><h1>Agenda</h1><p>Item 1: Approve contract</p></body></html>"
    text = extract_text("text/html; charset=utf-8", html)
    assert text == "Agenda\nItem 1: Approve contract"


def test_unknown_content_type_returns_none():
    assert extract_text("application/octet-stream", b"whatever bytes") is None


def test_empty_content_type_returns_none():
    assert extract_text("", b"whatever bytes") is None


def test_malformed_pdf_bytes_returns_none_rather_than_raising():
    assert extract_text("application/pdf", b"this is not a real pdf") is None


def test_empty_html_returns_none_not_empty_string():
    text = extract_text("text/html", b"<html><body></body></html>")
    assert text is None


def test_extracts_a_real_pdf_mislabeled_as_json():
    data = (FIXTURES / "pdf_mislabeled_as_json.pdf").read_bytes()
    text = extract_text("application/json; charset=utf-8", data)
    assert text is not None
    assert len(text) > 0


def test_sniff_kind_ignores_a_wrong_content_type_and_reads_magic_bytes():
    data = (FIXTURES / "pdf_mislabeled_as_json.pdf").read_bytes()
    assert sniff_kind(data) == "pdf"


def test_sniff_kind_returns_none_for_genuinely_unknown_bytes():
    assert sniff_kind(b"neither a pdf nor html") is None


def test_sniff_kind_detects_html_by_doctype():
    assert sniff_kind(b"<!DOCTYPE html><html><body>x</body></html>") == "html"


def test_extracted_html_text_has_null_bytes_stripped():
    """Real bug, live 2026-08-31: a real PrimeGov attachment's pypdf-
    extracted text contained a NUL byte, which Postgres' text type cannot
    store at all -- insert_version failed with 'unsupported Unicode escape
    sequence' (error 22P05), not a bug in this project's own SQL. Testing
    through HTML here since it's easy to construct with an embedded NUL;
    the strip happens in extract_text() itself, after either extractor,
    so it protects the PDF path identically."""
    html = "<html><body>Item 1\x00: Approve contract</body></html>".encode()
    text = extract_text("text/html", html)
    assert "\x00" not in text
    assert "Item 1" in text and "Approve contract" in text


def test_strip_null_bytes_removes_only_null_bytes():
    assert _strip_null_bytes("a\x00b\x00c") == "abc"
    assert _strip_null_bytes("no nulls here") == "no nulls here"
