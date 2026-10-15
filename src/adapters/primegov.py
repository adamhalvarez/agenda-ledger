"""PrimeGov, a Granicus-owned meeting-management platform.

Verified live 2026-08-30 for LA City Council: host lacity.primegov.com,
unauthenticated JSON at /api/v2/PublicPortal/ListUpcomingMeetings and
/api/v2/PublicPortal/ListArchivedMeetings?year=YYYY. One host/client slug
covers the full council plus every committee, distinguished by committeeId
on each meeting record (PrimeGov's analog of Legistar's EventBodyId).

There is no confirmed single "compiled agenda PDF" download endpoint --
several plausible URL patterns were probed live and all 404'd or soft-404'd.
What IS confirmed live is the per-meeting portal page
(/Portal/Meeting?meetingTemplateId=<id>), which server-renders the full
agenda as HTML (no JS execution needed) and, in the same response, the
individual agenda-item attachment links
(/api/compilemeetingattachmenthistory/historyattachment/?historyId=<uuid>,
each independently confirmed to return real files). So this adapter treats
the meeting page itself as the "agenda" document, and every attachment link
found in that same page as a separate "attachment" document. Revisit if
PrimeGov's compiled-PDF endpoint is ever confirmed -- it would be a strictly
better agenda artifact than the HTML page.
"""

import re

from bs4 import BeautifulSoup

from .base import DocumentRecord, MeetingRecord, SourceAdapter


class PrimeGovAdapter(SourceAdapter):
    """source_config:
    host                   PrimeGov host, e.g. "lacity.primegov.com" (required)
    committee_id           PrimeGov committeeId to filter to one body (optional)
    include_archived_year  also pull ListArchivedMeetings for this year (optional)
    """

    def discover(self):
        host = self.source_config["host"]
        committee_filter = self.source_config.get("committee_id")

        meetings = list(self._list(host, "ListUpcomingMeetings"))
        year = self.source_config.get("include_archived_year")
        if year:
            meetings += list(
                self._list(host, "ListArchivedMeetings", params={"year": year})
            )

        seen_meetings = set()
        for m in meetings:
            if committee_filter is not None and m.get("committeeId") != committee_filter:
                continue
            meeting_id = m.get("id")
            if meeting_id in seen_meetings:
                continue
            seen_meetings.add(meeting_id)

            document_list = m.get("documentList") or []
            if not document_list:
                continue  # nothing published for this meeting yet
            template_id = document_list[0].get("templateId")
            if template_id is None:
                continue

            meeting_date = (m.get("dateTime") or "").split("T")[0]
            if not meeting_date:
                continue

            meeting_page_url = f"https://{host}/Portal/Meeting?meetingTemplateId={template_id}"
            docs = [DocumentRecord("agenda", meeting_page_url)]
            docs.extend(self._attachments(meeting_page_url))
            yield MeetingRecord(meeting_date, meeting_page_url, tuple(docs))

    def _list(self, host, endpoint, params=None):
        url = f"https://{host}/api/v2/PublicPortal/{endpoint}"
        resp = self.http_get(url, params=params)
        return resp.json() or []

    def _attachments(self, meeting_page_url):
        resp = self.http_get(meeting_page_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        host = re.match(r"https?://([^/]+)", meeting_page_url).group(1)
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "compilemeetingattachmenthistory" not in href.lower():
                continue
            full_url = href if href.startswith("http") else f"https://{host}{href}"
            if full_url in seen:
                continue
            seen.add(full_url)
            yield DocumentRecord("attachment", full_url)
