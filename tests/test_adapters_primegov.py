from pathlib import Path

from src.adapters.primegov import PrimeGovAdapter
from src.httpclient import FixtureHttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "primegov"


def make_http():
    return FixtureHttpClient(
        {
            "https://lacity.primegov.com/api/v2/PublicPortal/ListUpcomingMeetings": (
                FIXTURES / "list_upcoming_meetings.json"
            ),
            "https://lacity.primegov.com/Portal/Meeting?meetingTemplateId=158303": (
                FIXTURES / "meeting_page_158303.html"
            ),
        }
    )


def test_discovers_meetings_from_the_live_recorded_list():
    http = make_http()
    adapter = PrimeGovAdapter({"host": "lacity.primegov.com"}, http)
    meetings = list(adapter.discover())

    assert len(meetings) == 1
    assert meetings[0].meeting_date == "2026-09-01"


def test_committee_id_filters_out_a_non_matching_body():
    http = make_http()
    adapter = PrimeGovAdapter({"host": "lacity.primegov.com", "committee_id": 999}, http)
    meetings = list(adapter.discover())

    assert meetings == []  # the fixture's only meeting is committeeId 1


def test_meeting_page_is_the_agenda_document():
    http = make_http()
    adapter = PrimeGovAdapter({"host": "lacity.primegov.com", "committee_id": 1}, http)
    meeting = next(iter(adapter.discover()))

    agenda_docs = [d for d in meeting.documents if d.doc_type == "agenda"]
    assert len(agenda_docs) == 1
    assert agenda_docs[0].source_url == (
        "https://lacity.primegov.com/Portal/Meeting?meetingTemplateId=158303"
    )


def test_attachments_parsed_from_the_meeting_page_html():
    http = make_http()
    adapter = PrimeGovAdapter({"host": "lacity.primegov.com", "committee_id": 1}, http)
    meeting = next(iter(adapter.discover()))

    attachments = [d for d in meeting.documents if d.doc_type == "attachment"]
    assert len(attachments) == 6  # the trimmed fixture keeps 6 real links
    assert all("compilemeetingattachmenthistory" in d.source_url for d in attachments)
    assert len({d.source_url for d in attachments}) == len(attachments)  # deduped
