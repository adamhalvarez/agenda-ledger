"""db.py has never run against live Supabase (no service-role key available
in this environment). These tests don't touch a network at all -- they
replace the supabase client with a fake that records every call -- but they
catch exactly the class of bug that would otherwise only surface on the
first real run: a wrong table name, a wrong on_conflict column list, or the
upsert-then-fallback-select logic not actually triggering on a duplicate.
postgrest-py's real request-building (verified separately, by reading
pre_upsert's source) confirms an ignored duplicate returns empty `data`,
which is the case these tests exercise.
"""

import src.db as db_mod


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self._filters = []

    def upsert(self, row, on_conflict="", ignore_duplicates=False):
        self.client.calls.append(
            {"op": "upsert", "table": self.name, "row": dict(row),
             "on_conflict": on_conflict, "ignore_duplicates": ignore_duplicates}
        )
        return self

    def insert(self, row):
        self.client.calls.append({"op": "insert", "table": self.name, "row": dict(row)})
        return self

    def select(self, cols):
        self.client.calls.append({"op": "select", "table": self.name, "cols": cols})
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        self.client.calls[-1].setdefault("filters", []).append((col, val))
        return self

    def order(self, col, desc=False):
        self.client.calls[-1]["order"] = (col, desc)
        return self

    def limit(self, n):
        self.client.calls[-1]["limit"] = n
        return self

    def execute(self):
        return self.client.next_response()


class FakeClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)

    def table(self, name):
        return FakeTable(self, name)

    def next_response(self):
        return self._responses.pop(0)


def use_fake_client(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr(db_mod, "get_client", lambda: fake)
    return fake


def test_sync_body_upserts_on_name_and_jurisdiction(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "body-1"}])])
    result = db_mod.sync_body("LA City Council", "City of LA", "primegov", {"host": "x"}, True)

    assert result == "body-1"
    call = fake.calls[0]
    assert call["table"] == "bodies"
    assert call["on_conflict"] == "name,jurisdiction"
    assert call["row"]["name"] == "LA City Council"
    assert call["row"]["source_type"] == "primegov"


def test_get_or_create_meeting_new_row(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "meeting-1"}])])
    meeting_id, created = db_mod.get_or_create_meeting("body-1", "2026-09-01", "https://x/agenda")

    assert (meeting_id, created) == ("meeting-1", True)
    call = fake.calls[0]
    assert call["table"] == "meetings"
    assert call["on_conflict"] == "body_id,meeting_date,source_url"
    assert call["ignore_duplicates"] is True
    # only the upsert call happened -- no fallback select needed
    assert len(fake.calls) == 1


def test_get_or_create_meeting_falls_back_to_select_on_duplicate(monkeypatch):
    fake = use_fake_client(
        monkeypatch,
        [FakeResult([]), FakeResult([{"id": "meeting-existing"}])],
    )
    meeting_id, created = db_mod.get_or_create_meeting("body-1", "2026-09-01", "https://x/agenda")

    assert (meeting_id, created) == ("meeting-existing", False)
    assert fake.calls[0]["op"] == "upsert"
    fallback = fake.calls[1]
    assert fallback["op"] == "select"
    assert fallback["table"] == "meetings"
    assert ("body_id", "body-1") in fallback["filters"]
    assert ("meeting_date", "2026-09-01") in fallback["filters"]
    assert ("source_url", "https://x/agenda") in fallback["filters"]


def test_get_or_create_document_new_row(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "doc-1"}])])
    doc_id, created = db_mod.get_or_create_document("meeting-1", "agenda", "https://x/agenda.pdf")

    assert (doc_id, created) == ("doc-1", True)
    call = fake.calls[0]
    assert call["table"] == "documents"
    assert call["on_conflict"] == "meeting_id,source_url"


def test_version_exists_true_and_false(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "v-1"}])])
    assert db_mod.version_exists("doc-1", "abc123") is True
    assert fake.calls[0]["table"] == "document_versions"
    assert ("document_id", "doc-1") in fake.calls[0]["filters"]
    assert ("sha256", "abc123") in fake.calls[0]["filters"]

    fake2 = use_fake_client(monkeypatch, [FakeResult([])])
    assert db_mod.version_exists("doc-1", "abc123") is False


def test_latest_version_returns_none_when_no_versions(monkeypatch):
    use_fake_client(monkeypatch, [FakeResult([])])
    assert db_mod.latest_version("doc-1") is None


def test_latest_version_orders_by_captured_at_desc(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "v-2", "sha256": "b", "captured_at": "t", "text_extracted": "T"}])])
    result = db_mod.latest_version("doc-1")

    assert result["id"] == "v-2"
    call = fake.calls[0]
    assert call["order"] == ("captured_at", True)
    assert call["limit"] == 1


def test_insert_version_includes_diff_summary(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "v-3"}])])
    version_id = db_mod.insert_version(
        "doc-1", "sha", "path/to/file.pdf", "extracted text", "v-2", "1 line added"
    )

    assert version_id == "v-3"
    call = fake.calls[0]
    assert call["op"] == "insert"
    assert call["table"] == "document_versions"
    assert call["row"]["diff_summary"] == "1 line added"
    assert call["row"]["prev_version_id"] == "v-2"


def test_insert_version_diff_summary_defaults_to_none(monkeypatch):
    fake = use_fake_client(monkeypatch, [FakeResult([{"id": "v-4"}])])
    db_mod.insert_version("doc-1", "sha", "path", "text", None)

    assert fake.calls[0]["row"]["diff_summary"] is None
    assert fake.calls[0]["row"]["prev_version_id"] is None
