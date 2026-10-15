"""The RSS fixture is a trimmed, real recorded response (5 of the original
100 items) from lacounty.granicus.com's ViewPublisherRSS feed, verified live
2026-08-30. The iCal fixture is hand-built to the RFC 5545 VEVENT shape --
no confirmed public iCal feed was found for any pilot body during research,
so this path is tested against the spec rather than a capture.
"""

from pathlib import Path

from src.adapters.granicus import GranicusAdapter
from src.httpclient import FixtureHttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "granicus"


def test_rss_yields_one_meeting_per_item():
    http = FixtureHttpClient({"https://example.granicus.com/rss": FIXTURES / "lacounty_bos.rss"})
    adapter = GranicusAdapter(
        {"feed_url": "https://example.granicus.com/rss", "feed_type": "rss"}, http
    )
    meetings = list(adapter.discover())

    assert len(meetings) == 5
    assert meetings[0].meeting_date == "2026-08-11"
    assert meetings[0].source_url == "https://lacounty.granicus.com/MediaPlayer.php?view_id=1&clip_id=12326"


def test_rss_doc_type_is_configurable():
    http = FixtureHttpClient({"https://example.granicus.com/rss": FIXTURES / "lacounty_bos.rss"})
    adapter = GranicusAdapter(
        {"feed_url": "https://example.granicus.com/rss", "feed_type": "rss", "doc_type": "minutes"},
        http,
    )
    meeting = next(iter(adapter.discover()))
    assert meeting.documents[0].doc_type == "minutes"


def test_rss_defaults_doc_type_to_other():
    http = FixtureHttpClient({"https://example.granicus.com/rss": FIXTURES / "lacounty_bos.rss"})
    adapter = GranicusAdapter(
        {"feed_url": "https://example.granicus.com/rss", "feed_type": "rss"}, http
    )
    meeting = next(iter(adapter.discover()))
    assert meeting.documents[0].doc_type == "other"


def test_ical_yields_one_meeting_per_vevent():
    http = FixtureHttpClient(
        {"https://example.granicus.com/cal.ics": FIXTURES / "lacounty_bos.ical"}
    )
    adapter = GranicusAdapter(
        {"feed_url": "https://example.granicus.com/cal.ics", "feed_type": "ical"}, http
    )
    meetings = list(adapter.discover())

    assert len(meetings) == 2
    assert meetings[0].meeting_date == "2026-08-11"
    assert meetings[1].meeting_date == "2026-08-18"
    assert meetings[0].documents[0].source_url == (
        "https://lacounty.granicus.com/MediaPlayer.php?view_id=1&clip_id=12326"
    )


def test_unknown_feed_type_raises():
    http = FixtureHttpClient({})
    adapter = GranicusAdapter(
        {"feed_url": "https://example.granicus.com/x", "feed_type": "carrier-pigeon"}, http
    )
    try:
        list(adapter.discover())
        assert False, "expected ValueError"
    except ValueError:
        pass
