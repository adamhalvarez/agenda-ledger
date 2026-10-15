#!/usr/bin/env python3
"""Phase 1 entrypoint.

Run:  python3 -m src.scan             fetches, hashes, writes to Supabase
      python3 -m src.scan --dry-run   fetches and hashes live sources, writes nothing

For each active body in bodies.yaml: discover meetings/documents via its
adapter, fetch each document's bytes, hash them, and write a new
document_versions row only when the hash is new for that document. An
unchanged source therefore produces zero new document_versions rows on a
second run against the same content -- that is Phase 1's definition of done.
"""

import argparse
import sys
from pathlib import Path

import yaml

from . import db
from .adapters import build_adapter
from .diffing import plain_language_summary
from .envload import load_dotenv
from .hashing import sha256_bytes
from .httpclient import real_http_get
from .storage import upload
from .text_extract import extract_text, sniff_kind

ROOT = Path(__file__).resolve().parent.parent

EXTENSION_MIME = {"pdf": "application/pdf", "html": "text/html"}


def load_bodies(path=None):
    path = Path(path) if path else ROOT / "bodies.yaml"
    data = yaml.safe_load(path.read_text())
    return data["bodies"]


def guess_extension(content, content_type, url):
    # Sniffed bytes win over a declared Content-Type -- verified live
    # 2026-08-30 that PrimeGov's attachment endpoint serves real PDFs
    # mislabeled as application/json. See text_extract.sniff_kind.
    kind = sniff_kind(content)
    if kind:
        return kind
    content_type = (content_type or "").lower()
    if "pdf" in content_type:
        return "pdf"
    if "html" in content_type:
        return "html"
    return Path(url.split("?")[0]).suffix.lstrip(".") or "bin"


def scan_body(body_config, dry_run=False):
    stats = {"meetings": 0, "documents": 0, "versions": 0, "unchanged": 0, "errors": 0}

    adapter = build_adapter(
        body_config["source_type"], body_config.get("source_config", {}), real_http_get
    )

    body_id = None
    if not dry_run:
        body_id = db.sync_body(
            body_config["name"],
            body_config["jurisdiction"],
            body_config["source_type"],
            body_config.get("source_config", {}),
            body_config.get("active", True),
        )

    for meeting in adapter.discover():
        stats["meetings"] += 1
        meeting_id = None
        if not dry_run:
            meeting_id, _ = db.get_or_create_meeting(
                body_id, meeting.meeting_date, meeting.source_url
            )

        for doc in meeting.documents:
            stats["documents"] += 1
            try:
                resp = real_http_get(doc.source_url)
            except Exception as exc:  # noqa: BLE001 -- external fetch, must not crash the run
                print(f"  ! fetch failed: {doc.source_url} ({exc})", file=sys.stderr)
                stats["errors"] += 1
                continue

            content = resp.content
            sha = sha256_bytes(content)
            content_type = resp.headers.get("Content-Type", "")

            if dry_run:
                print(f"  {meeting.meeting_date}  {doc.doc_type:<10}  {sha[:12]}  {doc.source_url}")
                continue

            try:
                document_id, _ = db.get_or_create_document(meeting_id, doc.doc_type, doc.source_url)

                if db.version_exists(document_id, sha):
                    stats["unchanged"] += 1
                    continue

                prev = db.latest_version(document_id)
                extension = guess_extension(content, content_type, doc.source_url)
                text = extract_text(content_type, content)
                upload_content_type = EXTENSION_MIME.get(extension, content_type) or "application/octet-stream"
                path = upload(sha, extension, content, upload_content_type)

                diff_summary = None
                if prev is not None:
                    diff_summary = plain_language_summary(prev.get("text_extracted"), text)

                db.insert_version(
                    document_id, sha, path, text, prev["id"] if prev else None, diff_summary
                )
                stats["versions"] += 1
            except Exception as exc:  # noqa: BLE001 -- one document's storage/DB
                # failure (e.g. an oversized upload) must not cost every other
                # document in this meeting, or every meeting after it in this
                # body -- same principle already applied to fetch failures
                # above and to a whole body's adapter failing in main().
                print(f"  ! document failed: {doc.source_url} ({exc})", file=sys.stderr)
                stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="discover and hash against live sources, write nothing to Postgres or storage",
    )
    args = parser.parse_args()

    load_dotenv()
    bodies = [b for b in load_bodies() if b.get("active", True)]

    totals = {"meetings": 0, "documents": 0, "versions": 0, "unchanged": 0, "errors": 0}
    for body_config in bodies:
        print(f"== {body_config['name']} ({body_config['source_type']}) ==")
        try:
            stats = scan_body(body_config, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 -- one body's adapter failing (a
            # network blip, a source changing shape, a stale hardcoded URL)
            # must not cost every other body its scan. Matches this project's
            # own established rule (see record-scanner's run.sh): a lane outage
            # is not allowed to cost the whole day's run.
            print(f"   ! body crashed: {exc}", file=sys.stderr)
            totals["errors"] += 1
            continue
        for k in totals:
            totals[k] += stats[k]
        print(
            f"   meetings={stats['meetings']} documents={stats['documents']} "
            f"new_versions={stats['versions']} unchanged={stats['unchanged']} "
            f"errors={stats['errors']}"
        )

    print(
        f"TOTAL  meetings={totals['meetings']} documents={totals['documents']} "
        f"new_versions={totals['versions']} unchanged={totals['unchanged']} "
        f"errors={totals['errors']}"
    )

    if totals["errors"] and not args.dry_run:
        sys.exit(1)


if __name__ == "__main__":
    main()
