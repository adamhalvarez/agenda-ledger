"""Diff generation for a new document version against its predecessor.

Deterministic on purpose, not an LLM call: document_versions has no columns
to record a model name, prompt version, or input hash for diff_summary (only
agenda_items does, for Phase 3 extraction), and the non-negotiable is that
every LLM output carries that provenance. Rather than add untracked LLM
output to an append-only table, diff_summary is computed the same way
text_extracted and sha256 are -- deterministically, so re-running it against
the same two versions always produces the same paragraph.
"""

import difflib


def unified_diff(old_text: str, new_text: str, context_lines: int = 3) -> str:
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile="previous", tofile="current", n=context_lines
    )
    return "".join(diff)


def plain_language_summary(old_text: str, new_text: str) -> str:
    """One paragraph, deterministic. Counts changed lines and names the most
    substantial change verbatim (rather than trying to paraphrase it), so
    the summary never asserts something the diff itself doesn't show."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)

    added, removed, changed_examples = 0, 0, []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        if tag == "replace" and j2 > j1:
            changed_examples.append(new_lines[j1].strip())
        elif tag == "insert" and j2 > j1:
            changed_examples.append(new_lines[j1].strip())
        elif tag == "delete" and i2 > i1:
            changed_examples.append(f"[removed] {old_lines[i1].strip()}")

    if added == 0 and removed == 0:
        return "No textual difference from the previous version."

    parts = [f"{added} line(s) added, {removed} line(s) removed compared to the previous version."]
    example = next((e for e in changed_examples if e), None)
    if example:
        snippet = example if len(example) <= 200 else example[:197] + "..."
        parts.append(f'Includes a change near: "{snippet}"')
    return " ".join(parts)
