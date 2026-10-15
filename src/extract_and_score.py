#!/usr/bin/env python3
"""Phase 3 entrypoint: extraction and salience.

Run:  python3 -m src.extract_and_score             extracts + scores, writes to Supabase
      python3 -m src.extract_and_score --report     prints the ranked least-covered-items report

For every document_version not yet extracted: run the LLM extraction pass
(src/extraction.py, which verifies every claim against the source text
before returning it), write agenda_items, then for each item's entities
query GDELT and the configured outlet RSS feed for mentions within a window
around the meeting date, write mentions, and compute+write a salience_score.

Every step here is non-fatal to the rest of the run, matching this
project's own established discipline (see record-scanner's run.sh): one
document's extraction failing, or one entity's GDELT query timing out,
should not cost every other document or entity its score.

Extraction is billed to the Claude subscription, not a metered API key
(src/extraction.py's call_llm_subscription_cli, the default), matching
record-scanner's own draft_assist.py. That means the cost that matters here is
subscription rate/session limits, not dollars per call -- MAX_EXTRACTIONS_
PER_RUN bounds how many document_versions one run will extract, the same
role MAX_DRAFTS_PER_DAY plays there. A run that hits the cap says so
and stops rather than silently truncating; the rest pick up next run.

A version whose text is identical to its predecessor's is skipped, never
sent to the model. Found live 2026-08-31 against the real pilot: PrimeGov's
meeting-page HTML embeds some volatile server-generated element (a CSRF
token, an asset bundle version string, or similar), so its raw bytes --
and therefore its sha256 -- differ on almost every fetch even though the
actual agenda content hasn't moved (confirmed: 13 real agenda pages
produced 40 document_versions across 3 scan runs, each new version's own
diff_summary correctly reading "No textual difference from the previous
version"). That's the right behavior for capture -- every fetch is still
faithfully hashed and stored, satisfying "every fetched file gets a
SHA256" for what was actually on the wire at that moment -- but re-running
extraction against textually-identical content on every single scan would
mean paying (in subscription usage, not dollars, but paying) for the exact
same LLM call over and over. The check compares text_extracted through
prev_version_id, so it costs one lookup, not another model call.
"""

import argparse
import os
import sys
from datetime import date

from . import db, mentions
from .envload import load_dotenv
from .extraction import extract_agenda_items
from .salience import compute_salience
from .textmatch import normalize_text

def _load_config():
    """Populates the module-level config globals below from os.environ.
    Called once at import (so the module works when used directly, e.g. in
    tests that monkeypatch these names with their own values) and again at
    the top of main() after load_dotenv() -- real bug, found live
    2026-08-31: these used to be plain module-level assignments evaluated
    once at import time, before main() ever calls load_dotenv(). Running
    via `python3 -m src.extract_and_score` therefore always saw
    OUTLET_RSS_FEED_URL as unset, silently, no matter what .env said --
    the CLI entrypoint's import happens before .env is ever read, and
    nothing re-read it afterward. Setting OUTLET_RSS_FEED_URL in .env had
    zero effect on any real run until this was fixed."""
    global OUTLET_RSS_FEED_URL, MENTIONS_WINDOW_DAYS, RECEIPTS_BASE_URL, MAX_EXTRACTIONS_PER_RUN, SOURCES_CONFIGURED
    OUTLET_RSS_FEED_URL = os.environ.get("OUTLET_RSS_FEED_URL") or None
    MENTIONS_WINDOW_DAYS = int(os.environ.get("MENTIONS_WINDOW_DAYS") or mentions.DEFAULT_WINDOW_DAYS)
    RECEIPTS_BASE_URL = os.environ.get("RECEIPTS_BASE_URL", "").rstrip("/")
    MAX_EXTRACTIONS_PER_RUN = int(os.environ.get("MAX_EXTRACTIONS_PER_RUN") or 20)
    # GDELT is always attempted; the outlet feed only if configured. Used to
    # tell "every configured source failed" apart from "one of two failed."
    SOURCES_CONFIGURED = 1 + (1 if OUTLET_RSS_FEED_URL else 0)


_load_config()


def extract_and_score_version(version, dry_run=False):
    """Returns per-version stats. Never raises -- a broken extraction or a
    broken mention lookup is recorded in the stats and the run continues."""
    stats = {"items": 0, "mentions": 0, "unmeasured_entities": 0, "errors": [], "skipped_duplicate_text": 0}

    text = version.get("text_extracted")
    if not text:
        return stats

    prev_version_id = version.get("prev_version_id")
    if prev_version_id:
        prev_text = db.version_text(prev_version_id)
        if prev_text is not None and normalize_text(prev_text) == normalize_text(text):
            stats["skipped_duplicate_text"] += 1
            return stats

    document_id = version["document_id"]
    meeting_id = db.meeting_id_for_document(document_id)
    meeting_date_str = db.meeting_date_for_meeting(meeting_id) if meeting_id else None
    meeting_date = date.fromisoformat(meeting_date_str) if meeting_date_str else date.today()

    try:
        extracted = extract_agenda_items(text)
    except Exception as exc:  # noqa: BLE001 -- an LLM call is an external boundary
        stats["errors"].append(f"extraction failed for version {version['id']}: {exc}")
        return stats

    for item, warnings in extracted:
        stats["items"] += 1
        for w in warnings:
            stats["errors"].append(f"version {version['id']} item {item.get('item_number')}: {w}")

        if dry_run:
            print(f"  item {item.get('item_number')}: {item['title'][:80]}")
            if warnings:
                for w in warnings:
                    print(f"    ! {w}")
            continue

        agenda_item_id = db.insert_agenda_item(meeting_id, version["id"], item)

        # Two real bugs, found live 2026-08-31 against the actual pilot,
        # fixed together: (1) this used to check stats["unmeasured_entities"],
        # a running total across every item in the whole document_version,
        # not this item -- one bad entity anywhere earlier in a multi-item
        # document (e.g. a real 38-item PrimeGov agenda) wrongly skipped
        # every later item too, even ones whose own entities measured fine.
        # (2) "measured" was conflated with "zero errors": with GDELT down
        # (a real, sustained outage, not hypothetical) but the outlet feed
        # up, an entity with one failed source and one *successful* zero-
        # result source was still marked unmeasured, discarding a real
        # measurement. any_entity_measured tracks, per item, whether at
        # least one configured source actually returned an answer for at
        # least one entity -- a successful empty result counts; only every
        # source failing for every entity means truly unmeasured.
        all_mentions = []
        any_entity_measured = not item.get("entities")  # nothing to measure -> vacuously measured
        for entity in item.get("entities", []):
            found, errors = mentions.fetch_mentions_for_entity(
                entity, meeting_date, outlet_feed_url=OUTLET_RSS_FEED_URL, window_days=MENTIONS_WINDOW_DAYS
            )
            all_mentions.extend(found)
            if len(errors) < SOURCES_CONFIGURED:
                any_entity_measured = True
            if errors:
                stats["unmeasured_entities"] += 1
                stats["errors"].extend(f"mentions for {entity!r}: {e}" for e in errors)

        distinct_urls = set()
        for m in all_mentions:
            db.insert_mention(agenda_item_id, m)
            distinct_urls.add(m["url"])
        stats["mentions"] += len(distinct_urls)

        if not any_entity_measured:
            # every entity's every configured source failed for this item
            # -- coverage is unmeasured, not zero, so no salience_scores
            # row is written at all.
            continue

        coverage_count = len(distinct_urls)
        ratio = compute_salience(record_activity=1, coverage_count=coverage_count)
        db.insert_salience_score(agenda_item_id, record_activity=1, coverage_count=coverage_count, ratio=ratio)

    return stats


def run(dry_run=False):
    # A large fetch so the cap below is checked against the real backlog
    # size, not an artifact of how many rows happened to be requested.
    candidates = db.versions_missing_extraction(limit=1000)
    held_back = max(0, len(candidates) - MAX_EXTRACTIONS_PER_RUN)
    to_process = candidates[:MAX_EXTRACTIONS_PER_RUN]

    totals = {"versions": 0, "items": 0, "mentions": 0, "unmeasured_entities": 0, "errors": 0, "skipped_duplicate_text": 0}
    for version in to_process:
        totals["versions"] += 1
        print(f"== document_version {version['id']} ==")
        stats = extract_and_score_version(version, dry_run=dry_run)
        totals["items"] += stats["items"]
        totals["mentions"] += stats["mentions"]
        totals["unmeasured_entities"] += stats["unmeasured_entities"]
        totals["errors"] += len(stats["errors"])
        totals["skipped_duplicate_text"] += stats["skipped_duplicate_text"]
        if stats["skipped_duplicate_text"]:
            print("  skipped: text identical to previous version, not re-extracted")
        for e in stats["errors"]:
            print(f"  ! {e}", file=sys.stderr)

    print(
        f"TOTAL  versions={totals['versions']} items={totals['items']} "
        f"mentions={totals['mentions']} unmeasured_entities={totals['unmeasured_entities']} "
        f"skipped_duplicate_text={totals['skipped_duplicate_text']} errors={totals['errors']}"
    )
    if held_back:
        print(
            f"CAPPED  {held_back} document_version(s) held back this run "
            f"(MAX_EXTRACTIONS_PER_RUN={MAX_EXTRACTIONS_PER_RUN}); they remain "
            f"unextracted and will be picked up next run."
        )


def receipt_url(document_version_id):
    if not RECEIPTS_BASE_URL:
        return f"(RECEIPTS_BASE_URL not set) /r/{document_version_id}"
    return f"{RECEIPTS_BASE_URL}/r/{document_version_id}"


def print_report(limit=25):
    rows = db.least_covered_items(limit=limit)
    if not rows:
        print("No scored agenda items yet.")
        return
    print(f"{'BODY':<40} {'ITEM':<8} {'COVERAGE':<9} {'RATIO':<8} TITLE")
    for r in rows:
        print(
            f"{r['body_name'][:40]:<40} {str(r.get('item_number') or ''):<8} "
            f"{r['coverage_count']:<9} {r['ratio']:<8.2f} {r['title'][:60]}"
        )
        print(f"  receipt: {receipt_url(r['document_version_id'])}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="extract and verify, write nothing to Postgres")
    parser.add_argument("--report", action="store_true", help="print the ranked least-covered-items report and exit")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    load_dotenv()
    _load_config()  # refresh, now that .env has actually been read

    if args.report:
        print_report(limit=args.limit)
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
