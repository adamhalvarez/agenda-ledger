"""extract_and_score.py's orchestration, tested with fakes for db, the LLM
extraction call, and mention fetching -- none of Phase 3's external
dependencies (Anthropic, GDELT, an outlet RSS feed) are available in this
environment, so this proves the wiring is correct rather than the external
calls themselves (those are covered separately: test_extraction.py against
a real recorded agenda, test_mentions.py against a real recorded GDELT
response).
"""

import sys

import src.extract_and_score as ext_mod


class FakeDB:
    def __init__(self, versions, version_texts=None):
        self._versions = versions
        self.meeting_ids = {"doc-1": "meeting-1", "doc-2": "meeting-1"}
        self.meeting_dates = {"meeting-1": "2026-09-01"}
        self.agenda_items = []
        self.mentions = []
        self.salience_scores = []
        self._next_id = 0
        self.least_covered_rows = []
        self._version_texts = version_texts or {}

    def _id(self, prefix):
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    def versions_missing_extraction(self, limit=100):
        return self._versions

    def meeting_id_for_document(self, document_id):
        return self.meeting_ids.get(document_id)

    def meeting_date_for_meeting(self, meeting_id):
        return self.meeting_dates.get(meeting_id)

    def insert_agenda_item(self, meeting_id, document_version_id, item):
        item_id = self._id("item")
        self.agenda_items.append({"id": item_id, "meeting_id": meeting_id, **item})
        return item_id

    def insert_mention(self, agenda_item_id, mention):
        self.mentions.append({"agenda_item_id": agenda_item_id, **mention})
        return self._id("mention")

    def insert_salience_score(self, agenda_item_id, record_activity, coverage_count, ratio):
        self.salience_scores.append(
            {"agenda_item_id": agenda_item_id, "record_activity": record_activity,
             "coverage_count": coverage_count, "ratio": ratio}
        )
        return self._id("score")

    def least_covered_items(self, limit=25):
        return self.least_covered_rows

    def version_text(self, version_id):
        return self._version_texts.get(version_id)


def patch_common(monkeypatch, fake_db, extracted, mention_results=None):
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: extracted)

    mention_results = mention_results if mention_results is not None else {}

    def fake_fetch(entity, meeting_date, outlet_feed_url=None, window_days=14):
        return mention_results.get(entity, ([], []))

    monkeypatch.setattr(ext_mod.mentions, "fetch_mentions_for_entity", fake_fetch)


def test_extract_and_score_version_writes_item_mentions_and_salience(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Approve contract"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Approve contract", "action_type": "approve", "entities": ["Acme Construction"],
          "addresses": [], "dollar_amount": 50000, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    mention_results = {
        "Acme Construction": (
            [{"source": "gdelt", "url": "https://a.example/1", "published_at": None, "matched_entity": "Acme Construction"}],
            [],
        )
    }
    patch_common(monkeypatch, fake_db, extracted, mention_results)

    stats = ext_mod.extract_and_score_version(version)

    assert stats["items"] == 1
    assert stats["mentions"] == 1
    assert stats["errors"] == []
    assert len(fake_db.agenda_items) == 1
    assert len(fake_db.mentions) == 1
    assert len(fake_db.salience_scores) == 1
    assert fake_db.salience_scores[0]["coverage_count"] == 1
    assert fake_db.salience_scores[0]["ratio"] == 1 / 1


def test_zero_coverage_item_still_gets_a_salience_score(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Receive and file"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Receive and file report", "action_type": "receive and file", "entities": ["Some Agency"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    mention_results = {"Some Agency": ([], [])}  # measured, genuinely zero
    patch_common(monkeypatch, fake_db, extracted, mention_results)

    ext_mod.extract_and_score_version(version)

    assert len(fake_db.salience_scores) == 1
    assert fake_db.salience_scores[0]["coverage_count"] == 0
    assert fake_db.salience_scores[0]["ratio"] == 1.0  # floor makes 0 coverage rank highest


def test_every_entity_measurement_failing_skips_salience_not_zero(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Approve"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Approve", "action_type": "approve", "entities": ["Some Agency"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    mention_results = {"Some Agency": ([], ["gdelt query failed: simulated"])}
    patch_common(monkeypatch, fake_db, extracted, mention_results)

    stats = ext_mod.extract_and_score_version(version)

    assert len(fake_db.agenda_items) == 1  # the item itself is still recorded
    assert len(fake_db.salience_scores) == 0  # but unmeasured coverage writes no score
    assert stats["unmeasured_entities"] == 1


def test_one_bad_entity_does_not_skip_a_later_items_score_in_the_same_document(monkeypatch):
    """Real bug, live 2026-08-31: the skip check used to read
    stats["unmeasured_entities"], a running total across every item in this
    whole document_version -- so item 1 having a bad entity wrongly skipped
    item 2's score too, even though item 2's own entity measured cleanly.
    Real multi-item documents are common (a real PrimeGov committee agenda
    had 38 items in one document_version); this must be per-item."""
    # Item 2's entity must succeed with a genuine zero (not just any
    # non-empty result) to actually exercise the bug: the old code's check
    # was `stats["unmeasured_entities"] > 0 and not all_mentions`, which
    # only misfires on item 2 when item 2's own result is *also* empty --
    # a non-empty item 2 result would pass either way, coincidentally.
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1 and 2"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Item 1", "action_type": "other", "entities": ["Bad Agency"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
        ({"title": "Item 2", "action_type": "other", "entities": ["Good Agency"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    mention_results = {
        "Bad Agency": ([], ["gdelt query failed: simulated"]),
        "Good Agency": ([], []),  # measured successfully, genuinely zero results
    }
    patch_common(monkeypatch, fake_db, extracted, mention_results)

    ext_mod.extract_and_score_version(version)

    assert len(fake_db.agenda_items) == 2
    assert len(fake_db.salience_scores) == 1  # item 2's score, not skipped by item 1's failure
    assert fake_db.salience_scores[0]["coverage_count"] == 0


def test_one_source_failing_does_not_discard_another_sources_successful_zero(monkeypatch):
    """Real bug, live 2026-08-31: with GDELT down but the outlet feed up,
    an entity where GDELT errored and the outlet feed successfully found
    zero results was still marked fully unmeasured -- discarding a real
    measurement (a genuine, confirmed zero) just because a redundant
    source also failed. SOURCES_CONFIGURED=2 simulates both GDELT and an
    outlet feed being configured; one error out of two sources means the
    entity was still measured by the other."""
    monkeypatch.setattr(ext_mod, "SOURCES_CONFIGURED", 2)
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Item 1", "action_type": "other", "entities": ["Some Agency"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    # one source (gdelt) errored, the other (outlet) succeeded with zero --
    # represented here as a single combined (found=[], errors=[1 error]) result
    mention_results = {"Some Agency": ([], ["gdelt query failed: simulated"])}
    patch_common(monkeypatch, fake_db, extracted, mention_results)

    ext_mod.extract_and_score_version(version)

    assert len(fake_db.salience_scores) == 1  # a real (if partial) measurement, not discarded
    assert fake_db.salience_scores[0]["coverage_count"] == 0


def test_items_with_no_entities_get_zero_coverage_and_a_score(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Closed session"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Closed session", "action_type": "closed session", "entities": [],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    patch_common(monkeypatch, fake_db, extracted, {})

    ext_mod.extract_and_score_version(version)

    assert len(fake_db.salience_scores) == 1
    assert fake_db.salience_scores[0]["coverage_count"] == 0


def test_dry_run_extracts_but_writes_nothing(monkeypatch, capsys):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Approve"}
    fake_db = FakeDB([version])
    extracted = [
        ({"title": "Approve contract", "action_type": "approve", "entities": ["Acme"],
          "addresses": [], "dollar_amount": None, "extraction_model": "m", "extraction_prompt_version": "v1"},
         ["dollar_amount 999 not found in source text, dropped"]),
    ]
    patch_common(monkeypatch, fake_db, extracted, {})

    stats = ext_mod.extract_and_score_version(version, dry_run=True)

    assert stats["items"] == 1
    assert fake_db.agenda_items == []
    assert fake_db.mentions == []
    assert fake_db.salience_scores == []
    out = capsys.readouterr().out
    assert "Approve contract" in out


def test_version_with_no_extracted_text_is_skipped(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": None}
    fake_db = FakeDB([version])
    calls = []
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: calls.append(text) or [])

    stats = ext_mod.extract_and_score_version(version)

    assert stats["items"] == 0
    assert calls == []  # never even called the model on empty text


def test_extraction_failure_is_recorded_not_raised(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "some text"}
    fake_db = FakeDB([version])
    monkeypatch.setattr(ext_mod, "db", fake_db)

    def broken_extract(text):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(ext_mod, "extract_agenda_items", broken_extract)

    stats = ext_mod.extract_and_score_version(version)

    assert stats["items"] == 0
    assert len(stats["errors"]) == 1
    assert "simulated API failure" in stats["errors"][0]


def test_print_report_handles_empty_and_populated_cases(monkeypatch, capsys):
    fake_db = FakeDB([])
    monkeypatch.setattr(ext_mod, "db", fake_db)

    ext_mod.print_report()
    assert "No scored agenda items" in capsys.readouterr().out

    fake_db.least_covered_rows = [
        {"body_name": "Test Body", "item_number": "1", "coverage_count": 0, "ratio": 1.0,
         "title": "Approve contract", "document_version_id": "v-1"},
    ]
    ext_mod.print_report()
    out = capsys.readouterr().out
    assert "Test Body" in out
    assert "Approve contract" in out
    assert "/r/v-1" in out


def test_receipt_url_notes_missing_base_url(monkeypatch):
    monkeypatch.setattr(ext_mod, "RECEIPTS_BASE_URL", "")
    assert "not set" in ext_mod.receipt_url("abc-123")

    monkeypatch.setattr(ext_mod, "RECEIPTS_BASE_URL", "https://receipts.example.com")
    assert ext_mod.receipt_url("abc-123") == "https://receipts.example.com/r/abc-123"


def test_run_processes_everything_when_under_the_cap(monkeypatch, capsys):
    versions = [{"id": f"v-{i}", "document_id": "doc-1", "text_extracted": None} for i in range(3)]
    fake_db = FakeDB(versions)
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "MAX_EXTRACTIONS_PER_RUN", 5)

    ext_mod.run()

    out = capsys.readouterr().out
    assert "TOTAL  versions=3" in out
    assert "CAPPED" not in out


def test_run_stops_at_the_cap_and_reports_what_was_held_back(monkeypatch, capsys):
    versions = [{"id": f"v-{i}", "document_id": "doc-1", "text_extracted": None} for i in range(5)]
    fake_db = FakeDB(versions)
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "MAX_EXTRACTIONS_PER_RUN", 2)

    ext_mod.run()

    out = capsys.readouterr().out
    assert "TOTAL  versions=2" in out
    assert "CAPPED  3 document_version(s) held back" in out


def test_run_default_cap_is_a_reasonable_positive_number():
    assert ext_mod.MAX_EXTRACTIONS_PER_RUN > 0


def test_identical_text_to_previous_version_skips_extraction_without_calling_the_model(monkeypatch):
    """Real case, live 2026-08-31: a real PrimeGov meeting page's raw bytes
    (and therefore sha256) differ on almost every fetch -- some volatile
    server-generated token, not real content -- while the extracted text is
    byte-identical. Re-extracting that is a wasted subscription-billed LLM
    call for content already extracted. This must not touch the model."""
    version = {
        "id": "v-2", "document_id": "doc-1", "text_extracted": "Item 1: Approve contract",
        "prev_version_id": "v-1",
    }
    fake_db = FakeDB([version], version_texts={"v-1": "Item 1: Approve contract"})
    calls = []
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: calls.append(text) or [])

    stats = ext_mod.extract_and_score_version(version)

    assert calls == []  # the model was never called
    assert stats["skipped_duplicate_text"] == 1
    assert stats["items"] == 0
    assert fake_db.agenda_items == []


def test_whitespace_only_difference_from_previous_version_still_skips(monkeypatch):
    version = {
        "id": "v-2", "document_id": "doc-1", "text_extracted": "Item 1:   Approve  contract\n\n",
        "prev_version_id": "v-1",
    }
    fake_db = FakeDB([version], version_texts={"v-1": "Item 1: Approve contract"})
    calls = []
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: calls.append(text) or [])

    stats = ext_mod.extract_and_score_version(version)

    assert calls == []
    assert stats["skipped_duplicate_text"] == 1


def test_genuinely_different_text_from_previous_version_still_extracts(monkeypatch):
    version = {
        "id": "v-2", "document_id": "doc-1", "text_extracted": "Item 1: Approve contract for $75,000",
        "prev_version_id": "v-1",
    }
    fake_db = FakeDB([version], version_texts={"v-1": "Item 1: Approve contract for $50,000"})
    extracted = [
        ({"title": "Approve contract", "action_type": "approve", "entities": [],
          "addresses": [], "dollar_amount": 75000, "extraction_model": "m", "extraction_prompt_version": "v1"}, []),
    ]
    calls = []
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: calls.append(text) or extracted)
    monkeypatch.setattr(ext_mod.mentions, "fetch_mentions_for_entity", lambda *a, **k: ([], []))

    stats = ext_mod.extract_and_score_version(version)

    assert len(calls) == 1  # the model WAS called this time
    assert stats["skipped_duplicate_text"] == 0
    assert stats["items"] == 1


def test_first_version_of_a_document_has_no_prev_to_compare_and_always_extracts(monkeypatch):
    version = {"id": "v-1", "document_id": "doc-1", "text_extracted": "Item 1: Approve", "prev_version_id": None}
    fake_db = FakeDB([version])
    calls = []
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: calls.append(text) or [])

    stats = ext_mod.extract_and_score_version(version)

    assert len(calls) == 1
    assert stats["skipped_duplicate_text"] == 0


def test_run_totals_report_skipped_duplicate_text(monkeypatch, capsys):
    version = {
        "id": "v-2", "document_id": "doc-1", "text_extracted": "same text", "prev_version_id": "v-1",
    }
    fake_db = FakeDB([version], version_texts={"v-1": "same text"})
    monkeypatch.setattr(ext_mod, "db", fake_db)
    monkeypatch.setattr(ext_mod, "extract_agenda_items", lambda text: [])

    ext_mod.run()

    out = capsys.readouterr().out
    assert "skipped_duplicate_text=1" in out
    assert "skipped: text identical to previous version" in out


def test_main_picks_up_env_vars_set_by_load_dotenv(monkeypatch):
    """Real bug, live 2026-08-31: OUTLET_RSS_FEED_URL (and friends) used to
    be plain module-level assignments evaluated once at import time --
    before main() ever calls load_dotenv(). Running via
    `python3 -m src.extract_and_score` therefore always saw
    OUTLET_RSS_FEED_URL as unset no matter what .env said: setting it in
    .env had silently zero effect on every real run until this was fixed.
    This simulates what load_dotenv() does (populate os.environ) and
    confirms main() picks the new value up afterward, not just at import.

    _load_config() mutates module globals directly (via `global`), which
    monkeypatch.setattr can't auto-revert the way it does a plain
    attribute assignment -- explicitly restored in `finally`, or this
    leaks OUTLET_RSS_FEED_URL/SOURCES_CONFIGURED into every later test in
    the whole suite, not just this file."""
    original = (ext_mod.OUTLET_RSS_FEED_URL, ext_mod.SOURCES_CONFIGURED)
    try:
        monkeypatch.setattr(ext_mod, "load_dotenv", lambda: None)
        monkeypatch.setenv("OUTLET_RSS_FEED_URL", "https://example.com/feed.rss")
        monkeypatch.setattr(sys, "argv", ["extract_and_score.py", "--report"])
        monkeypatch.setattr(ext_mod, "print_report", lambda limit=25: None)

        assert ext_mod.OUTLET_RSS_FEED_URL != "https://example.com/feed.rss"  # precondition: stale from import

        ext_mod.main()

        assert ext_mod.OUTLET_RSS_FEED_URL == "https://example.com/feed.rss"
        assert ext_mod.SOURCES_CONFIGURED == 2
    finally:
        ext_mod.OUTLET_RSS_FEED_URL, ext_mod.SOURCES_CONFIGURED = original
