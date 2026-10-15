"""GDELT + pilot outlet RSS mention fetching, windowed around a meeting date.

GDELT DOC 2.0 API (api.gdeltproject.org/api/v2/doc/doc), mode=artlist,
verified live 2026-08-30: returns {"articles": [{"url", "title", "seendate"
(format YYYYMMDDTHHMMSSZ), "domain", ...]}}. startdatetime/enddatetime take
YYYYMMDDHHMMSS, no separators -- also verified live.

GDELT is confirmed flaky under light real use in this same session: a
second query issued minutes after a successful first one failed outright
(curl exit 35, SSL connect error), matching record-scanner's own documented
finding that GDELT penalizes an IP regardless of pacing. A failed GDELT
measurement is therefore unmeasured here, never zero -- the same "null is
not zero" rule this whole system already runs on. Zero coverage is exactly
the condition that ranks an item highest, so a broken fetch that silently
reads as zero would put the broken thing first.

GDELT has no AND operator and ANDs terms by whitespace (same quirk
record-scanner's fetch.py works around) -- query one entity at a time, never a
boolean expression.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests

from .textmatch import phrase_present

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_WINDOW_DAYS = 14
TIMEOUT = 30


class MentionFetchError(Exception):
    """A source could not be measured. Callers must treat this as
    unmeasured, never as zero mentions."""


def _gdelt_datetime(d):
    return d.strftime("%Y%m%d%H%M%S")


def _parse_gdelt_seendate(seendate):
    if not seendate:
        return None
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").isoformat()
    except ValueError:
        return None


def query_gdelt(entity, meeting_date, window_days=DEFAULT_WINDOW_DAYS, http_get=None, max_records=25):
    """meeting_date: a date object. Raises MentionFetchError on any
    failure -- callers must not coerce that into zero mentions."""
    http_get = http_get or requests.get
    start = meeting_date - timedelta(days=window_days)
    end = meeting_date + timedelta(days=window_days)
    params = {
        "query": f'"{entity}"',
        "mode": "artlist",
        "maxrecords": max_records,
        "format": "json",
        "sort": "datedesc",
        "startdatetime": _gdelt_datetime(start),
        "enddatetime": _gdelt_datetime(end),
    }
    try:
        resp = http_get(GDELT_URL, params=params, timeout=TIMEOUT)
        data = resp.json()
    except Exception as exc:
        raise MentionFetchError(f"gdelt query failed for {entity!r}: {exc}") from exc

    return [
        {
            "source": "gdelt",
            "url": a.get("url"),
            "published_at": _parse_gdelt_seendate(a.get("seendate")),
            "matched_entity": entity,
        }
        for a in (data.get("articles") or [])
        if a.get("url")
    ]


def query_outlet_rss(entity, meeting_date, feed_url, window_days=DEFAULT_WINDOW_DAYS, http_get=None):
    """Matches on the feed's own title/description text, whole-word
    boundary (not a bare substring -- see record-scanner's CLAUDE.md on why a
    hyphenated slug like "cfpb-nominations" false-positives inside
    "cfpb-nominations-2027" under naive substring matching). RSS feeds
    generally carry only a title and short description, not full article
    text, so this can under-match a mention that names the entity only in
    the article body -- a known limitation, not a bug."""
    http_get = http_get or requests.get
    start = meeting_date - timedelta(days=window_days)
    end = meeting_date + timedelta(days=window_days)

    try:
        resp = http_get(feed_url)
        root = ET.fromstring(resp.text)
    except Exception as exc:
        raise MentionFetchError(f"outlet rss query failed for {feed_url!r}: {exc}") from exc

    mentions = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pubdate = (item.findtext("pubDate") or "").strip()
        if not link or not pubdate:
            continue
        if not (phrase_present(entity, title) or phrase_present(entity, description)):
            continue
        try:
            published = parsedate_to_datetime(pubdate)
        except (TypeError, ValueError):
            continue
        if not (start <= published.date() <= end):
            continue
        mentions.append(
            {
                "source": "outlet_rss",
                "url": link,
                "published_at": published.isoformat(),
                "matched_entity": entity,
            }
        )
    return mentions


def fetch_mentions_for_entity(entity, meeting_date, outlet_feed_url=None, window_days=DEFAULT_WINDOW_DAYS,
                               gdelt_http_get=None, rss_http_get=None):
    """Best-effort across both sources: one source failing doesn't lose the
    other. Returns (mentions, errors) rather than raising, so a caller
    scoring many entities in a loop can record which measurements failed
    -- to keep unmeasured, never zero -- without a Phase 3 run of many
    entities dying on the first flaky GDELT call."""
    mentions, errors = [], []

    try:
        mentions.extend(query_gdelt(entity, meeting_date, window_days, http_get=gdelt_http_get))
    except MentionFetchError as exc:
        errors.append(str(exc))

    if outlet_feed_url:
        try:
            mentions.extend(
                query_outlet_rss(entity, meeting_date, outlet_feed_url, window_days, http_get=rss_http_get)
            )
        except MentionFetchError as exc:
            errors.append(str(exc))

    return mentions, errors
