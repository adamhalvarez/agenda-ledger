"""Offline test of Phase 1's actual definition of done: a scan against
unchanged sources produces zero new document_versions rows. This exercises
scan.py's real orchestration logic end to end, with fixture-backed HTTP and
an in-memory fake standing in for Postgres/storage -- proving the dedup
logic itself, not Supabase's unique constraint (that half is a live check,
see README's Phase 1 section).
"""

from pathlib import Path

import src.scan as scan_mod
from src.hashing import sha256_bytes
from src.httpclient import FixtureHttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "legistar"

BODY_CONFIG = {
    "name": "Example Legistar City Council",
    "jurisdiction": "Example City",
    "source_type": "legistar",
    "active": True,
    "source_config": {"client": "example"},
}


class FakeDB:
    def __init__(self):
        self.bodies = {}
        self.meetings = {}
        self.documents = {}
        self.versions = {}  # document_id -> list of {"id", "sha256"}
        self._n = 0

    def _next_id(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n}"

    def sync_body(self, name, jurisdiction, source_type, source_config, active):
        key = (name, jurisdiction)
        if key not in self.bodies:
            self.bodies[key] = self._next_id("body")
        return self.bodies[key]

    def get_or_create_meeting(self, body_id, meeting_date, source_url):
        key = (body_id, meeting_date, source_url)
        created = key not in self.meetings
        if created:
            self.meetings[key] = self._next_id("meeting")
        return self.meetings[key], created

    def get_or_create_document(self, meeting_id, doc_type, source_url):
        key = (meeting_id, source_url)
        created = key not in self.documents
        if created:
            self.documents[key] = self._next_id("doc")
            self.versions[self.documents[key]] = []
        return self.documents[key], created

    def version_exists(self, document_id, sha256):
        return any(v["sha256"] == sha256 for v in self.versions[document_id])

    def latest_version(self, document_id):
        versions = self.versions[document_id]
        return versions[-1] if versions else None

    def insert_version(
        self, document_id, sha256, storage_path, text_extracted, prev_version_id, diff_summary=None
    ):
        version_id = self._next_id("version")
        self.versions[document_id].append(
            {"id": version_id, "sha256": sha256, "text_extracted": text_extracted, "diff_summary": diff_summary}
        )
        return version_id


def make_http():
    return FixtureHttpClient(
        {
            "https://webservices.legistar.com/v1/example/Events": FIXTURES / "events.json",
            # both event.EventAgendaFile / EventMinutesFile urls in the fixture
            # resolve through this one fake document body for the test
            "https://example.legistar.com/View.ashx?M=A&ID=900001": FIXTURES / "events.json",
            "https://example.legistar.com/View.ashx?M=M&ID=900002": FIXTURES / "events.json",
            "https://example.legistar.com/View.ashx?M=A&ID=900010": FIXTURES / "events.json",
        }
    )


def run_scan(monkeypatch, fake_db):
    http = make_http()
    monkeypatch.setattr(scan_mod, "real_http_get", http)
    monkeypatch.setattr(scan_mod, "db", fake_db)
    monkeypatch.setattr(scan_mod, "upload", lambda sha, ext, data, ct: f"fake/{sha}.{ext}")
    return scan_mod.scan_body(BODY_CONFIG, dry_run=False)


def test_first_run_writes_versions_second_run_writes_none(monkeypatch):
    fake_db = FakeDB()

    first = run_scan(monkeypatch, fake_db)
    assert first["versions"] == 3  # 2 agenda + 1 minutes across the fixture's 2 dated events
    assert first["unchanged"] == 0

    second = run_scan(monkeypatch, fake_db)
    assert second["versions"] == 0
    assert second["unchanged"] == 3
    assert second["meetings"] == first["meetings"]
    assert second["documents"] == first["documents"]


def test_changed_content_produces_a_new_version_linked_to_the_prior_one(monkeypatch):
    fake_db = FakeDB()
    run_scan(monkeypatch, fake_db)

    # every document in this fixture currently resolves to the same fixture
    # file (events.json); hashing is content-based, so pointing one document
    # at genuinely different bytes must register as a new version.
    http = make_http()
    http.routes["https://example.legistar.com/View.ashx?M=A&ID=900001"] = (
        FIXTURES / "events_changed.json"
    )
    monkeypatch.setattr(scan_mod, "real_http_get", http)
    monkeypatch.setattr(scan_mod, "db", fake_db)
    monkeypatch.setattr(scan_mod, "upload", lambda sha, ext, data, ct: f"fake/{sha}.{ext}")

    result = scan_mod.scan_body(BODY_CONFIG, dry_run=False)
    assert result["versions"] == 1
    assert result["unchanged"] == 2

    # the new version links back to the one it replaced
    body_id = fake_db.bodies[("Example Legistar City Council", "Example City")]
    meeting_id = fake_db.meetings[
        (body_id, "2026-08-18", "https://example.legistar.com/MeetingDetail.aspx?ID=40001")
    ]
    doc_id = fake_db.documents[
        (meeting_id, "https://example.legistar.com/View.ashx?M=A&ID=900001")
    ]
    versions = fake_db.versions[doc_id]
    assert len(versions) == 2
    expected_sha = sha256_bytes(Path(FIXTURES / "events_changed.json").read_bytes())
    assert versions[-1]["sha256"] == expected_sha


def test_one_document_failing_after_fetch_does_not_stop_the_rest(monkeypatch, capsys):
    """Real bug, found live 2026-08-31 against the actual pilot: a
    PrimeGov attachment upload hit Supabase Storage's payload-size limit,
    and because only the HTTP fetch step was wrapped in a try/except, that
    single document's failure propagated all the way out of scan_body and
    aborted the rest of that body's meetings and documents entirely --
    the crash was only ever caught at the whole-body level in main(). This
    fixture's 3 documents share one underlying file (so they can't be
    distinguished by hash), so the fake `upload` fails on its first call
    (call-count based) and succeeds after -- proving the failure is
    isolated to the one document, not just coincidentally recoverable."""
    fake_db = FakeDB()
    http = make_http()
    monkeypatch.setattr(scan_mod, "real_http_get", http)
    monkeypatch.setattr(scan_mod, "db", fake_db)

    calls = {"n": 0}

    def flaky_upload(sha, ext, data, ct):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Payload too large")
        return f"fake/{sha}.{ext}"

    monkeypatch.setattr(scan_mod, "upload", flaky_upload)

    result = scan_mod.scan_body(BODY_CONFIG, dry_run=False)

    assert result["errors"] == 1
    assert result["versions"] == 2  # the other 2 documents still succeeded
    assert "document failed" in capsys.readouterr().err
