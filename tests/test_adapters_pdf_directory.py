from pathlib import Path

from src.adapters.pdf_directory import PdfDirectoryAdapter
from src.httpclient import FixtureHttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "pdf_directory"


def test_lacounty_bos_index_yields_dated_agendas():
    http = FixtureHttpClient(
        {"https://bos.lacounty.gov/board-meeting-agendas/": FIXTURES / "lacounty_bos_index.html"}
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_url": "https://bos.lacounty.gov/board-meeting-agendas/",
            "date_format": "%m%d%y",
        },
        http,
    )
    meetings = list(adapter.discover())

    assert meetings  # the real recorded page has agendas on it
    assert all(d.doc_type == "agenda" for m in meetings for d in m.documents)
    # every yielded url really is a .pdf
    assert all(d.source_url.lower().endswith(".pdf") for m in meetings for d in m.documents)
    # a real meeting date from the recorded page comes through correctly
    assert "2026-09-01" in {m.meeting_date for m in meetings}


def test_lacounty_bos_skips_pdfs_with_no_dateable_filename():
    http = FixtureHttpClient(
        {"https://bos.lacounty.gov/board-meeting-agendas/": FIXTURES / "lacounty_bos_index.html"}
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_url": "https://bos.lacounty.gov/board-meeting-agendas/",
            "date_format": "%m%d%y",
        },
        http,
    )
    urls = {d.source_url for m in adapter.discover() for d in m.documents}
    # "Revised Rules of the Board 2.6.26 final.pdf" has no 6- or 8-digit run
    assert not any("Revised%20Rules%20of%20the%20Board" in u for u in urls)


def test_lapd_bpc_index_yields_dated_agendas():
    http = FixtureHttpClient(
        {
            "https://www.lapdonline.org/police-commission-agenda-archives-2026/": (
                FIXTURES / "lapd_bpc_2026_index.html"
            )
        }
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_url": "https://www.lapdonline.org/police-commission-agenda-archives-2026/",
            "date_format": "%m%d%y",
        },
        http,
    )
    meetings = list(adapter.discover())

    assert meetings
    assert "2026-08-25" in {m.meeting_date for m in meetings}  # from 082526.pdf


def test_lapd_bpc_skips_unrelated_pdfs():
    http = FixtureHttpClient(
        {
            "https://www.lapdonline.org/police-commission-agenda-archives-2026/": (
                FIXTURES / "lapd_bpc_2026_index.html"
            )
        }
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_url": "https://www.lapdonline.org/police-commission-agenda-archives-2026/",
            "date_format": "%m%d%y",
        },
        http,
    )
    urls = {d.source_url for m in adapter.discover() for d in m.documents}
    # "LAPD-Strategic-Plan-2025-2026-2.pdf" has no bounded 6/8-digit run
    assert not any("Strategic-Plan" in u for u in urls)


def test_dedupes_repeated_hrefs():
    http = FixtureHttpClient(
        {"https://example.gov/agendas/": FIXTURES / "lacounty_bos_index.html"}
    )
    adapter = PdfDirectoryAdapter(
        {"index_url": "https://example.gov/agendas/", "date_format": "%m%d%y"}, http
    )
    urls = [d.source_url for m in adapter.discover() for d in m.documents]
    assert len(urls) == len(set(urls))


def test_index_urls_plural_merges_results_from_multiple_pages():
    http = FixtureHttpClient(
        {
            "https://example.gov/2026/": FIXTURES / "lapd_bpc_2026_index.html",
            "https://example.gov/2027/": FIXTURES / "lacounty_bos_index.html",
        }
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_urls": ["https://example.gov/2026/", "https://example.gov/2027/"],
            "date_format": "%m%d%y",
        },
        http,
    )
    meetings = list(adapter.discover())
    # real dates confirmed present in each individual fixture earlier in
    # this file -- both must show up when the pages are merged
    dates = {m.meeting_date for m in meetings}
    assert "2026-08-25" in dates  # from the LAPD fixture
    assert "2026-09-01" in dates  # from the LA County fixture


def test_index_urls_one_page_failing_does_not_lose_the_others():
    # only one of the two configured pages is registered with the fixture
    # client -- the other's fetch raises, simulating a not-yet-published
    # next-year archive page 404ing.
    http = FixtureHttpClient(
        {"https://example.gov/2026/": FIXTURES / "lapd_bpc_2026_index.html"}
    )
    adapter = PdfDirectoryAdapter(
        {
            "index_urls": ["https://example.gov/2026/", "https://example.gov/2027-not-yet-published/"],
            "date_format": "%m%d%y",
        },
        http,
    )
    meetings = list(adapter.discover())
    assert any(m.meeting_date == "2026-08-25" for m in meetings)
