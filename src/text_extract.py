"""Text extraction for hashed snapshots. Best-effort: a document that fails
to extract still gets its hash, storage row, and version -- capture must
never depend on extraction succeeding. Exceptions here are caught broadly on
purpose: this runs against arbitrary real-world PDFs and HTML from sources
this project does not control, which is exactly the external boundary where
defensive handling belongs, unlike internal logic elsewhere in this repo.
"""

import io

from bs4 import BeautifulSoup
from pypdf import PdfReader


def sniff_kind(data: bytes):
    """Identify a file by its actual bytes rather than a declared
    Content-Type. Necessary, not paranoid: PrimeGov's attachment endpoint
    (verified live 2026-08-30) serves genuine PDFs -- confirmed by the
    %PDF- magic bytes -- labeled Content-Type: application/json. Trusting
    the header there would silently extract no text from every PrimeGov
    attachment."""
    if data[:5] == b"%PDF-":
        return "pdf"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return "html"
    return None


def _kind_from_content_type(content_type: str):
    content_type = (content_type or "").lower()
    if "pdf" in content_type:
        return "pdf"
    if "html" in content_type:
        return "html"
    return None


def extract_text(content_type: str, data: bytes):
    kind = sniff_kind(data) or _kind_from_content_type(content_type)
    if kind == "pdf":
        text = _extract_pdf(data)
    elif kind == "html":
        text = _extract_html(data)
    else:
        return None
    return _strip_null_bytes(text) if text else text


def _strip_null_bytes(text: str) -> str:
    """Postgres' text type cannot store a NUL byte at all (confirmed live
    2026-08-31: a real captured PrimeGov attachment's pypdf-extracted text
    contained one, and the insert failed with 'unsupported Unicode escape
    sequence' -- Postgres error 22P05, not this project's own code). A NUL
    inside extracted text is a PDF-extraction artifact from a binary or
    malformed stream, never meaningful content, so stripping it costs
    nothing real."""
    return text.replace("\x00", "")


def _extract_pdf(data):
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        return text or None
    except Exception:
        return None


def _extract_html(data):
    try:
        soup = BeautifulSoup(data, "html.parser")
        text = soup.get_text("\n", strip=True)
        return text or None
    except Exception:
        return None
