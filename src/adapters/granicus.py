"""Granicus RSS and iCal meeting feeds.

Granicus has no unified public API; access is per-client, and even within
one client, RSS/iCal feeds are not guaranteed to carry an agenda link at
all. Verified live 2026-08-30 against LA County's Board of Supervisors feed
(lacounty.granicus.com/ViewPublisherRSS.php?view_id=1&mode=podcast): its RSS
items carry a MediaPlayer clip page and a raw video enclosure, no agenda PDF
whatsoever -- LA County's actual agendas are plain PDFs on bos.lacounty.gov,
handled by pdf_directory.py instead. This adapter is still built and tested
per the spec, for Granicus clients whose feeds do carry an agenda link
(common on smaller clients whose ViewPublisher page lists an "Agenda" column
per meeting), and it is wired into bodies.yaml.example.

The iCal fixture this adapter is tested against is hand-built to the RFC
5545 VEVENT shape, not a recorded response -- no confirmed public iCal feed
was found for any of the three pilot bodies during research. The format
itself is a fixed IETF standard, so building a conformant fixture from the
spec is safe; a live Granicus iCal export should still be diffed against it
before this path is trusted for a real body.
"""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .base import DocumentRecord, MeetingRecord, SourceAdapter

_VEVENT_RE = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.S)
_FIELD_RE = re.compile(r"^([A-Z]+)(?:;[^:]*)?:(.*)$")


class GranicusAdapter(SourceAdapter):
    """source_config:
    feed_url    the RSS or iCal feed URL                              (required)
    feed_type   "rss" or "ical"                                       (required)
    doc_type    doc_type to assign the linked page, default "other"   (optional)
    """

    def discover(self):
        feed_type = self.source_config["feed_type"]
        if feed_type == "rss":
            yield from self._discover_rss()
        elif feed_type == "ical":
            yield from self._discover_ical()
        else:
            raise ValueError(f"GranicusAdapter: unknown feed_type {feed_type!r}")

    def _discover_rss(self):
        doc_type = self.source_config.get("doc_type", "other")
        resp = self.http_get(self.source_config["feed_url"])
        root = ET.fromstring(resp.text)
        for item in root.iter("item"):
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")
            if link_el is None or not (link_el.text or "").strip():
                continue
            if pubdate_el is None or not (pubdate_el.text or "").strip():
                continue
            link = link_el.text.strip()
            try:
                meeting_date = parsedate_to_datetime(pubdate_el.text.strip()).date().isoformat()
            except (TypeError, ValueError):
                continue
            yield MeetingRecord(meeting_date, link, (DocumentRecord(doc_type, link),))

    def _discover_ical(self):
        doc_type = self.source_config.get("doc_type", "other")
        resp = self.http_get(self.source_config["feed_url"])
        for block in _VEVENT_RE.findall(resp.text):
            fields = {}
            for line in block.strip().splitlines():
                line = line.strip()
                m = _FIELD_RE.match(line)
                if m:
                    fields[m.group(1)] = m.group(2).strip()
            dtstart = fields.get("DTSTART")
            url = fields.get("URL")
            if not dtstart or not url:
                continue
            date_digits = dtstart[:8]
            if len(date_digits) != 8 or not date_digits.isdigit():
                continue
            meeting_date = f"{date_digits[0:4]}-{date_digits[4:6]}-{date_digits[6:8]}"
            yield MeetingRecord(meeting_date, url, (DocumentRecord(doc_type, url),))
