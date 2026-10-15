from pathlib import Path

from src.adapters.legistar import LegistarAdapter
from src.httpclient import FixtureHttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "legistar"


def make_http():
    return FixtureHttpClient(
        {
            "https://webservices.legistar.com/v1/example/Events": FIXTURES / "events.json",
        }
    )


def test_yields_one_meeting_per_event_with_files():
    http = make_http()
    adapter = LegistarAdapter({"client": "example"}, http)
    meetings = list(adapter.discover())

    # events.json has 3 events; the third has no agenda/minutes file at all
    # and must be skipped, since a document row needs a document.
    assert len(meetings) == 2
    assert {m.meeting_date for m in meetings} == {"2026-08-18", "2026-08-20"}


def test_agenda_and_minutes_become_separate_documents():
    http = make_http()
    adapter = LegistarAdapter({"client": "example"}, http)
    meetings = {m.meeting_date: m for m in adapter.discover()}

    full = meetings["2026-08-18"]
    assert {d.doc_type for d in full.documents} == {"agenda", "minutes"}

    agenda_only = meetings["2026-08-20"]
    assert [d.doc_type for d in agenda_only.documents] == ["agenda"]


def test_event_body_id_filters_to_one_body():
    http = make_http()
    adapter = LegistarAdapter({"client": "example", "event_body_id": 101}, http)
    meetings = list(adapter.discover())

    assert len(meetings) == 1
    assert meetings[0].meeting_date == "2026-08-20"


def test_meeting_source_url_prefers_insite_url():
    http = make_http()
    adapter = LegistarAdapter({"client": "example"}, http)
    meetings = {m.meeting_date: m for m in adapter.discover()}
    assert meetings["2026-08-18"].source_url == (
        "https://example.legistar.com/MeetingDetail.aspx?ID=40001"
    )
