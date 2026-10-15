"""gdelt_articles.json is a real recorded response from GDELT's live doc/artlist
API (fetched 2026-08-30). outlet_rss.xml is hand-built, from before a pilot
outlet was named, but still exercises real RSS parsing and the whole-word-
boundary entity matching this module depends on.

laist_latest_news.xml is a real recorded response (fetched 2026-08-31) from
the actual pilot outlet's feed, LAist (https://laist.com/rss/latest-news.rss,
confirmed live: standard RSS 2.0, Brightspot CMS), trimmed to 5 of its 12
items -- the regression fixture for OUTLET_RSS_FEED_URL now being live in
production.
"""

from datetime import date
from pathlib import Path

import pytest

from src.httpclient import FixtureHttpClient
from src.mentions import (
    MentionFetchError,
    fetch_mentions_for_entity,
    query_gdelt,
    query_outlet_rss,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mentions"


def test_query_gdelt_parses_real_recorded_articles():
    http = FixtureHttpClient({"https://api.gdeltproject.org/api/v2/doc/doc": FIXTURES / "gdelt_articles.json"})
    mentions = query_gdelt("Los Angeles County", date(2026, 8, 28), http_get=http)

    assert len(mentions) == 5
    assert all(m["source"] == "gdelt" for m in mentions)
    assert all(m["matched_entity"] == "Los Angeles County" for m in mentions)
    assert mentions[0]["url"].startswith("https://")
    assert mentions[0]["published_at"] == "2026-08-28T21:15:00"


def test_query_gdelt_raises_mention_fetch_error_on_failure():
    def broken_get(*a, **k):
        raise ConnectionError("simulated SSL failure")

    with pytest.raises(MentionFetchError):
        query_gdelt("anything", date(2026, 8, 28), http_get=broken_get)


def test_query_outlet_rss_matches_whole_word_only():
    http = FixtureHttpClient({"https://example-outlet.org/rss": FIXTURES / "outlet_rss.xml"})
    mentions = query_outlet_rss("Acme Construction", date(2026, 9, 1), "https://example-outlet.org/rss", http_get=http)

    urls = {m["url"] for m in mentions}
    assert "https://example-outlet.org/acme-contract" in urls
    # "AcmeCorp" must not false-positive match "Acme" as a naive substring would
    assert "https://example-outlet.org/acmecorp-merger" not in urls
    assert "https://example-outlet.org/weather" not in urls


def test_query_outlet_rss_excludes_items_outside_the_window():
    http = FixtureHttpClient({"https://example-outlet.org/rss": FIXTURES / "outlet_rss.xml"})
    mentions = query_outlet_rss(
        "Acme Construction", date(2026, 9, 1), "https://example-outlet.org/rss",
        window_days=14, http_get=http,
    )
    urls = {m["url"] for m in mentions}
    assert "https://example-outlet.org/old-story" not in urls  # dated 2024, outside any 14-day window


def test_query_outlet_rss_raises_on_malformed_feed():
    class BadResp:
        text = "not xml at all <<<"

    with pytest.raises(MentionFetchError):
        query_outlet_rss("Acme", date(2026, 9, 1), "https://x", http_get=lambda *a, **k: BadResp())


def test_fetch_mentions_for_entity_combines_both_sources():
    gdelt_http = FixtureHttpClient({"https://api.gdeltproject.org/api/v2/doc/doc": FIXTURES / "gdelt_articles.json"})
    rss_http = FixtureHttpClient({"https://example-outlet.org/rss": FIXTURES / "outlet_rss.xml"})

    mentions, errors = fetch_mentions_for_entity(
        "Acme Construction", date(2026, 9, 1),
        outlet_feed_url="https://example-outlet.org/rss",
        gdelt_http_get=gdelt_http, rss_http_get=rss_http,
    )

    assert errors == []
    sources = {m["source"] for m in mentions}
    assert sources == {"gdelt", "outlet_rss"}


def test_fetch_mentions_for_entity_one_source_failing_does_not_lose_the_other():
    def broken_gdelt(*a, **k):
        raise ConnectionError("simulated")

    rss_http = FixtureHttpClient({"https://example-outlet.org/rss": FIXTURES / "outlet_rss.xml"})

    mentions, errors = fetch_mentions_for_entity(
        "Acme Construction", date(2026, 9, 1),
        outlet_feed_url="https://example-outlet.org/rss",
        gdelt_http_get=broken_gdelt, rss_http_get=rss_http,
    )

    assert len(errors) == 1
    assert any(m["source"] == "outlet_rss" for m in mentions)
    assert not any(m["source"] == "gdelt" for m in mentions)


def test_fetch_mentions_for_entity_skips_outlet_when_no_feed_configured():
    gdelt_http = FixtureHttpClient({"https://api.gdeltproject.org/api/v2/doc/doc": FIXTURES / "gdelt_articles.json"})
    mentions, errors = fetch_mentions_for_entity(
        "Los Angeles County", date(2026, 8, 28), outlet_feed_url=None, gdelt_http_get=gdelt_http
    )
    assert errors == []
    assert all(m["source"] == "gdelt" for m in mentions)


def test_query_outlet_rss_parses_the_real_pilot_outlet_feed():
    http = FixtureHttpClient(
        {"https://laist.com/rss/latest-news.rss": FIXTURES / "laist_latest_news.xml"}
    )
    mentions = query_outlet_rss("NPR", date(2026, 8, 31), "https://laist.com/rss/latest-news.rss", window_days=1, http_get=http)

    assert len(mentions) == 1
    assert mentions[0]["url"] == (
        "https://laist.com/news/climate-environment/tracking-deaths-from-heat-staggering-hidden-toll"
    )
    assert mentions[0]["source"] == "outlet_rss"


def test_query_outlet_rss_against_the_real_pilot_feed_finds_nothing_for_an_unrelated_entity():
    http = FixtureHttpClient(
        {"https://laist.com/rss/latest-news.rss": FIXTURES / "laist_latest_news.xml"}
    )
    mentions = query_outlet_rss(
        "Acme Construction", date(2026, 8, 31), "https://laist.com/rss/latest-news.rss",
        window_days=1, http_get=http,
    )
    assert mentions == []
