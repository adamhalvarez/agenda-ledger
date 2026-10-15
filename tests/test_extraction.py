"""lacounty_supplemental_agenda.{pdf,txt} is a real LA County Board of
Supervisors supplemental agenda (fetched 2026-08-30, from the same
bos.lacounty.gov index the pdf_directory adapter crawls). llm_response.json
is NOT a live model call -- it's a hand-written stand-in for what a model
response would look like, deliberately built with one fully accurate item
and one item carrying three fabricated claims (a dollar amount, an address,
and an entity name, none of which appear anywhere in the real text), to
prove verify_extraction() actually catches and strips hallucinations rather
than just trusting whatever the model returns.
"""

import hashlib
import json
from pathlib import Path

import src.extraction as extraction_mod
from src.extraction import (
    EXTRACTION_PROMPT_TEMPLATE,
    PROMPT_VERSION,
    ExtractionError,
    call_llm_subscription_cli,
    extract_agenda_items,
    verify_extraction,
)

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
SOURCE_TEXT = (FIXTURES / "lacounty_supplemental_agenda.txt").read_text()
LLM_RESPONSE = (FIXTURES / "llm_response.json").read_text()


def fake_call_llm(prompt, model):
    return LLM_RESPONSE


def test_extract_agenda_items_returns_one_pair_per_item():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm)
    assert len(results) == 2


def test_fully_grounded_item_verifies_with_no_warnings():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm)
    item, warnings = results[0]

    assert item["item_number"] == "1-P"
    assert item["dollar_amount"] == 549532.83
    assert set(item["entities"]) == {"Department of Parks and Recreation", "Supervisor Solis"}
    assert warnings == []


def test_hallucinated_dollar_amount_is_dropped():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm)
    item, warnings = results[1]

    assert item["dollar_amount"] is None
    assert any("250000000" in w or "250000000.0" in w for w in warnings)


def test_hallucinated_address_is_dropped():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm)
    item, warnings = results[1]

    assert item["addresses"] == []
    assert any("742 Evergreen Terrace" in w for w in warnings)


def test_hallucinated_entity_is_dropped_but_real_entities_survive():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm)
    item, warnings = results[1]

    assert "Consumer Protection Bureau of Los Angeles" not in item["entities"]
    assert "Supervisor Barger" in item["entities"]
    assert "Supervisor Horvath" in item["entities"]
    assert any("Consumer Protection Bureau of Los Angeles" in w for w in warnings)


def test_every_item_carries_model_and_prompt_version():
    results = extract_agenda_items(SOURCE_TEXT, call_llm=fake_call_llm, model="test-model-x")
    for item, _ in results:
        assert item["extraction_model"] == "test-model-x"
        assert item["extraction_prompt_version"] == "v1"


def test_empty_source_text_short_circuits_without_calling_the_model():
    calls = []

    def spy_call_llm(prompt, model):
        calls.append(prompt)
        return LLM_RESPONSE

    assert extract_agenda_items("", call_llm=spy_call_llm) == []
    assert calls == []


def test_malformed_json_raises_extraction_error():
    def broken_call_llm(prompt, model):
        return "not json at all"

    try:
        extract_agenda_items(SOURCE_TEXT, call_llm=broken_call_llm)
        assert False, "expected ExtractionError"
    except ExtractionError:
        pass


def test_markdown_code_fence_is_stripped():
    def fenced_call_llm(prompt, model):
        return f"```json\n{LLM_RESPONSE}\n```"

    results = extract_agenda_items(SOURCE_TEXT, call_llm=fenced_call_llm)
    assert len(results) == 2


def test_valid_json_followed_by_trailing_content_still_parses():
    """Real case, live 2026-08-31: the same prompt against the same short
    input (a LAPD cancellation notice) returned clean JSON on one run and,
    on another, valid JSON followed by extra trailing content that made
    strict json.loads() reject the entire response. LAPD cancellations are
    routine (roughly a third of posted meetings in the pilot's own dry
    run), so this had to be handled, not shrugged off as a one-off."""
    response = '{"items": []}\nNote: this meeting was cancelled, so there are no items.'
    results = extract_agenda_items(SOURCE_TEXT, call_llm=lambda p, m: response)
    assert results == []


def test_leading_prose_before_the_json_object_is_tolerated():
    response = 'Here is the extracted JSON:\n{"items": []}'
    results = extract_agenda_items(SOURCE_TEXT, call_llm=lambda p, m: response)
    assert results == []


def test_response_with_no_json_object_at_all_raises_extraction_error():
    def broken_call_llm(prompt, model):
        return "I could not find any agenda items in this text."

    try:
        extract_agenda_items(SOURCE_TEXT, call_llm=broken_call_llm)
        assert False, "expected ExtractionError"
    except ExtractionError:
        pass


def test_unknown_action_type_normalizes_to_other():
    response = json.dumps({"items": [{"title": "Something", "action_type": "REVISE", "entities": [], "addresses": []}]})
    results = extract_agenda_items(SOURCE_TEXT, call_llm=lambda p, m: response)
    assert results[0][0]["action_type"] == "other"


def test_known_action_type_is_normalized_lowercase():
    response = json.dumps({"items": [{"title": "Something", "action_type": "APPROVE", "entities": [], "addresses": []}]})
    results = extract_agenda_items(SOURCE_TEXT, call_llm=lambda p, m: response)
    assert results[0][0]["action_type"] == "approve"


def test_verify_extraction_is_directly_usable_without_the_llm_layer():
    item = {"title": "x", "action_type": "other", "dollar_amount": 999999999, "addresses": [], "entities": ["Nonexistent Corp"]}
    cleaned, warnings = verify_extraction(item, "some unrelated source text")
    assert cleaned["dollar_amount"] is None
    assert cleaned["entities"] == []
    assert len(warnings) == 2


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_call_llm_subscription_cli_extracts_the_result_field(monkeypatch):
    envelope = json.dumps({"is_error": False, "result": '{"items": []}', "total_cost_usd": 0.01})
    monkeypatch.setattr(
        extraction_mod.subprocess, "run", lambda *a, **k: FakeCompletedProcess(0, stdout=envelope)
    )
    assert call_llm_subscription_cli("prompt text") == '{"items": []}'


def test_call_llm_subscription_cli_passes_allowedtools_empty_and_the_model(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0, stdout=json.dumps({"is_error": False, "result": "{}"}))

    monkeypatch.setattr(extraction_mod.subprocess, "run", fake_run)
    call_llm_subscription_cli("prompt text", model="claude-sonnet-5")

    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "prompt text" in cmd
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == ""
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert "--bare" not in cmd


def test_call_llm_subscription_cli_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        extraction_mod.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(1, stderr="boom"),
    )
    try:
        call_llm_subscription_cli("prompt text")
        assert False, "expected ExtractionError"
    except ExtractionError as e:
        assert "boom" in str(e)


def test_call_llm_subscription_cli_raises_on_malformed_envelope(monkeypatch):
    monkeypatch.setattr(
        extraction_mod.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(0, stdout="not json"),
    )
    try:
        call_llm_subscription_cli("prompt text")
        assert False, "expected ExtractionError"
    except ExtractionError:
        pass


def test_call_llm_subscription_cli_raises_when_envelope_reports_is_error(monkeypatch):
    envelope = json.dumps({"is_error": True, "result": "something went wrong"})
    monkeypatch.setattr(
        extraction_mod.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(0, stdout=envelope),
    )
    try:
        call_llm_subscription_cli("prompt text")
        assert False, "expected ExtractionError"
    except ExtractionError as e:
        assert "something went wrong" in str(e)


def test_prompt_template_is_pinned_to_its_version():
    """The non-negotiable is that every LLM output can be re-run and
    audited via (extraction_prompt_version, document_versions.sha256).
    That trail is only meaningful if "v1" always means the same actual
    prompt text -- if this fails, EXTRACTION_PROMPT_TEMPLATE changed
    without PROMPT_VERSION being bumped alongside it, which would make
    "v1" silently refer to two different prompts across time. Bump
    PROMPT_VERSION and update the hash below together, deliberately,
    rather than letting this test rot into an ignored red."""
    assert PROMPT_VERSION == "v1"
    actual_hash = hashlib.sha256(EXTRACTION_PROMPT_TEMPLATE.encode()).hexdigest()
    assert actual_hash == "d23301fac1d147e7eab0d23f1281b0edd9a023e0aeec244c5b0d96c305885045", (
        f"EXTRACTION_PROMPT_TEMPLATE changed without bumping PROMPT_VERSION "
        f"(still {PROMPT_VERSION!r}). New hash: {actual_hash}"
    )


def test_extract_agenda_items_defaults_to_the_subscription_cli_backend(monkeypatch):
    """The whole point of this swap is that nothing calls the metered API
    by default -- confirm extract_agenda_items() reaches for the CLI
    backend, not call_llm_anthropic, when no call_llm is injected."""
    called = {}

    def fake_cli(prompt, model):
        called["hit"] = True
        return LLM_RESPONSE

    monkeypatch.setattr(extraction_mod, "call_llm_subscription_cli", fake_cli)
    extract_agenda_items(SOURCE_TEXT)
    assert called.get("hit") is True
