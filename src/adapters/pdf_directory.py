"""Generic crawler for a page that links dated PDF agendas directly, with no
API of any kind. Covers both LA County Board of Supervisors and the LA
Board of Police Commissioners, verified live 2026-08-30:

  LA County BOS   https://bos.lacounty.gov/board-meeting-agendas/
                  PDFs on a CMS asset host, filenames like "090126.pdf" or
                  "Agenda 090126_links.pdf" (MMDDYY embedded in the name).

  LA Police Comm  https://www.lapdonline.org/police-commission-agenda-archives-2026/
                  PDFs on Azure blob storage, filenames like "082526.pdf" or
                  "080426-CANCELLATION.pdf" (also MMDDYY). This host sits
                  behind bot detection that blocks a bare/UA-less request --
                  see httpclient.py's default headers.

Same adapter, same date_format ("%m%d%y") for both -- the two bodies differ
only in index_url, which is exactly the point: a third body in this shape
is a bodies.yaml entry, not new code.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import DocumentRecord, MeetingRecord, SourceAdapter

# A run of exactly 6 or 8 digits, not embedded in a longer digit run --
# bounded so "032420260" (from a filename like a calendar PDF's name) or a
# bare 4-digit year doesn't get misread as a date. Filenames carrying a real
# meeting date consistently isolate it this way; everything else (policy
# PDFs, FAQs, strategic plans) reliably fails this check and is skipped.
_DATE_RUN_RE = re.compile(r"(?<!\d)(\d{8}|\d{6})(?!\d)")


class PdfDirectoryAdapter(SourceAdapter):
    """source_config:
    index_url    a single page listing links to PDFs                     (one of index_url/index_urls required)
    index_urls   a list of such pages, results merged and deduped        (one of index_url/index_urls required)
    date_format  strptime format matching the digit run                  (required, e.g. "%m%d%y")
    doc_type     doc_type to assign each PDF, default "agenda"           (optional)
    link_suffix  href suffix to treat as a document, default ".pdf"      (optional)

    index_urls exists for LAPD's Police Commission archive, which is one
    page per calendar year: listing both the current and next year's page
    means the adapter keeps working across a year boundary with no
    bodies.yaml edit and no code change, rather than silently going stale
    every January. A not-yet-published next-year page 404s -- handled
    per-URL, not fatal to the pages that do exist (and, one layer up,
    scan.py's main() no longer lets any single body's adapter failure take
    down the rest of the run either)."""

    def discover(self):
        index_urls = self.source_config.get("index_urls") or [self.source_config["index_url"]]
        date_format = self.source_config["date_format"]
        doc_type = self.source_config.get("doc_type", "agenda")
        suffix = self.source_config.get("link_suffix", ".pdf").lower()

        seen_urls = set()
        for index_url in index_urls:
            try:
                resp = self.http_get(index_url)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception as exc:  # noqa: BLE001 -- e.g. next year's page not published yet
                print(f"  ! pdf_directory: could not read {index_url}: {exc}")
                continue

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().split("?")[0].endswith(suffix):
                    continue
                full_url = urljoin(index_url, href)
                if full_url in seen_urls:
                    continue

                m = _DATE_RUN_RE.search(href) or _DATE_RUN_RE.search(a.get_text() or "")
                if not m:
                    continue
                try:
                    meeting_date = datetime.strptime(m.group(1), date_format).date().isoformat()
                except ValueError:
                    continue

                seen_urls.add(full_url)
                yield MeetingRecord(meeting_date, full_url, (DocumentRecord(doc_type, full_url),))
