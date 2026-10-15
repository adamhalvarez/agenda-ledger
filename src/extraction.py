"""LLM extraction of structured agenda items from a document version's
captured text, with a mandatory verification pass before anything is
handed to a caller for storage.

Provenance without a new column: the non-negotiable is that every LLM
output is stored alongside model name, prompt version, and input hash.
agenda_items has extraction_model and extraction_prompt_version columns but
no extraction_input_hash -- deliberately not duplicated, since
agenda_items.document_version_id already references a document_versions
row whose sha256 is the exact, immutable input this extraction ran
against. (extraction_prompt_version, document_versions.sha256) via that FK
is the audit trail; a separate hash column would just be a copy of data one
join away.

Verification is not optional and cannot be bypassed by a caller: every item
returned by extract_agenda_items() has already had every claimed dollar
amount and entity checked against the source text, and anything that
doesn't verify is dropped rather than passed through. This is aimed
directly at Phase 3's own definition of done -- "a spot check of ten
extractions ... showing no hallucinated dollar amounts or entities" is
exactly what this guards against automatically, on every extraction, not
just the ten someone happens to check by hand.
"""

import json
import re
import subprocess

from .textmatch import normalize_text, phrase_present

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "claude-sonnet-5"
# 120s was too tight, found live 2026-08-31: a real ~35k-char committee
# agenda (38 items, many with dense closed-session case detail) timed out
# at 120s. Not everyday-sized, but real -- Budget and Finance-type
# committee agendas run long. 300s gives room without being unbounded.
CLI_TIMEOUT_SECONDS = 300

# Non-exhaustive on purpose -- the build spec names these as examples
# ("award, approve, amend, receive and file, closed session, and so on").
# An action type outside this list is normalized to "other" rather than
# rejected; the raw model wording isn't discarded, see _normalize_item.
KNOWN_ACTION_TYPES = {
    "award", "approve", "amend", "receive and file", "closed session",
    "adopt", "authorize", "allocate", "waive", "deny", "continue", "other",
}

EXTRACTION_PROMPT_TEMPLATE = """You are extracting structured agenda items from the text of a local government meeting agenda. Read the text below and identify every distinct agenda item.

For each item, extract ONLY what is explicitly stated in the text. Never infer, estimate, or complete a partial figure. If a detail is not present, omit that field or use null -- an omission is correct, a guess is not.

Return ONLY a JSON object of this exact shape, no other text:
{{
  "items": [
    {{
      "item_number": "<the item's number/letter as written, or null>",
      "title": "<a short, factual title for the item>",
      "action_type": "<one of: award, approve, amend, receive and file, closed session, adopt, authorize, allocate, waive, deny, continue, other>",
      "dollar_amount": <a plain number, no currency symbol or commas, or null if none is stated>,
      "addresses": [<street addresses explicitly stated, as written>],
      "entities": [<people, companies, or agencies explicitly named in connection with this item, as written>]
    }}
  ]
}}

AGENDA TEXT:
{text}
"""


class ExtractionError(Exception):
    pass


def build_prompt(text: str) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(text=text)


def call_llm_anthropic(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Direct Anthropic API call, metered per token. Not the default --
    see call_llm_subscription_cli, which is."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def call_llm_subscription_cli(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Runs the prompt through the Claude Code CLI in print mode, billed
    against the caller's existing Claude subscription rather than metered
    Anthropic API usage -- the same pattern record-scanner's own
    draft_assist.py already uses for its own drafting step ("billed to the
    subscription"). This is the default backend for exactly that reason:
    it avoids adding a second, separately-billed cost to this pilot.

    Verified live 2026-08-31: `claude -p <prompt> --output-format json
    --allowedTools ""` returns a JSON envelope whose "result" field is the
    model's raw text response, "is_error" reports failure, and
    "total_cost_usd" is the API-equivalent spend (informational only --
    nothing is actually billed per call under a subscription).

    --allowedTools "" disables tool use entirely: this is a text-in,
    JSON-out task, and an unattended cron job has no business invoking
    tools. Deliberately NOT --bare: --bare forces
    ANTHROPIC_API_KEY/apiKeyHelper auth and explicitly never reads
    OAuth/keychain credentials, which would silently defeat the whole
    point of using this function instead of call_llm_anthropic.

    Each call is a fresh CLI session with no continuity to any other call,
    so the fixed system-prompt overhead this incurs (observed: ~10k cache
    creation tokens, a few cents of API-equivalent cost) is paid on every
    single invocation, not just the first -- real, but small relative to
    what a batch of a few dozen documents actually costs, and unavoidable
    without giving up subscription billing. extract_and_score.py caps
    calls per run for the same reason record-scanner caps its own subscription
    -billed calls per day: to protect the account's rate/session limits,
    not dollars.
    """
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--allowedTools", ""],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ExtractionError(f"claude CLI exited {result.returncode}: {result.stderr.strip()[:500]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"claude CLI did not return a JSON envelope: {exc}") from exc

    if envelope.get("is_error"):
        raise ExtractionError(f"claude CLI reported an error: {envelope.get('result')!r}")

    return envelope.get("result", "")


def _strip_code_fence(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"```\s*$", "", stripped)
    return stripped.strip()


def _parse_response(raw: str):
    """Tolerant of anything surrounding the JSON object, not just markdown
    fences. Found live 2026-08-31: the same prompt against the same input
    returned clean JSON on one run and, on another, valid JSON followed by
    trailing content that made strict json.loads() reject the whole
    response (a real LA Police Commission cancellation notice -- a common
    case, not an edge one; LAPD cancels roughly a third of its posted
    meetings). json.JSONDecoder.raw_decode from the first "{" parses the
    first complete JSON value and ignores whatever follows it, rather than
    demanding the entire response be nothing but JSON."""
    stripped = _strip_code_fence(raw)
    start = stripped.find("{")
    if start == -1:
        raise ExtractionError(f"model response contained no JSON object: {raw[:200]!r}")
    try:
        parsed, _ = json.JSONDecoder().raw_decode(stripped[start:])
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"model did not return valid JSON: {exc}") from exc
    items = parsed.get("items")
    if not isinstance(items, list):
        raise ExtractionError('expected a JSON object with an "items" list')
    return items


def _normalize_item(raw_item: dict) -> dict:
    action_type = (raw_item.get("action_type") or "").strip().lower()
    return {
        "item_number": raw_item.get("item_number"),
        "title": (raw_item.get("title") or "").strip(),
        "action_type": action_type if action_type in KNOWN_ACTION_TYPES else "other",
        "dollar_amount": raw_item.get("dollar_amount"),
        "addresses": [a for a in (raw_item.get("addresses") or []) if a],
        "entities": [e for e in (raw_item.get("entities") or []) if e],
    }


def _amount_variants(amount):
    """Every plausible way this figure could appear verbatim in source
    text: with/without a thousands separator, with/without a currency
    symbol, with/without cents when the value is a whole number."""
    variants = set()
    for value in ({amount, round(amount)} if amount == int(amount) else {amount}):
        plain = f"{value:,.2f}" if isinstance(value, float) and value != int(value) else f"{int(value):,}"
        bare = plain.replace(",", "")
        for v in (plain, bare):
            variants.add(v)
            variants.add(f"${v}")
    return variants


def verify_extraction(item: dict, source_text: str):
    """Returns (cleaned_item, warnings). Never raises -- a claim that
    doesn't verify is dropped, not fatal to the rest of the item.

    Matching goes through textmatch.phrase_present: whitespace-tolerant
    (pypdf commonly breaks a multi-word entity across a PDF line wrap,
    confirmed in a real captured LA County agenda) and boundary-aware
    without requiring a word-class transition on both sides of a
    punctuation-terminated phrase (a parenthetical abbreviation like
    "(DCBA)" broke naive \\b\\b anchoring in a real live run -- see
    textmatch.py for the full story)."""
    warnings = []
    cleaned = dict(item)
    source_text = normalize_text(source_text)

    amount = cleaned.get("dollar_amount")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            warnings.append(f"dollar_amount {amount!r} was not numeric, dropped")
            amount = None
        else:
            if not any(v in source_text for v in _amount_variants(amount)):
                warnings.append(f"dollar_amount {amount} not found in source text, dropped")
                amount = None
    cleaned["dollar_amount"] = amount

    verified_entities = [e for e in cleaned.get("entities", []) if phrase_present(e, source_text)]
    dropped = [e for e in cleaned.get("entities", []) if e not in verified_entities]
    if dropped:
        warnings.append(f"entities not found in source text, dropped: {dropped}")
    cleaned["entities"] = verified_entities

    verified_addresses = [a for a in cleaned.get("addresses", []) if normalize_text(a) in source_text]
    dropped_addr = [a for a in cleaned.get("addresses", []) if a not in verified_addresses]
    if dropped_addr:
        warnings.append(f"addresses not found in source text, dropped: {dropped_addr}")
    cleaned["addresses"] = verified_addresses

    return cleaned, warnings


def extract_agenda_items(text_extracted: str, call_llm=None, model: str = DEFAULT_MODEL):
    """Returns a list of (item_dict, warnings) pairs. item_dict is ready
    for insertion into agenda_items (plus meeting_id/document_version_id,
    which this module doesn't know); warnings is empty when every claim in
    the item verified against text_extracted."""
    if not text_extracted:
        return []

    call_llm = call_llm or call_llm_subscription_cli
    raw = call_llm(build_prompt(text_extracted), model)
    raw_items = _parse_response(raw)

    results = []
    for raw_item in raw_items:
        item = _normalize_item(raw_item)
        cleaned, warnings = verify_extraction(item, text_extracted)
        cleaned["extraction_model"] = model
        cleaned["extraction_prompt_version"] = PROMPT_VERSION
        results.append((cleaned, warnings))
    return results
