"""Shared phrase-presence matching: whitespace/quote-tolerant and
boundary-aware, without requiring a word-class transition on a
punctuation-terminated edge.

Three real bugs found here, live, all against real captured government
text, none hypothetical:

1. pypdf commonly breaks a multi-word entity across a PDF line wrap
   ("Department of Parks \\nand Recreation", a real LA County agenda) --
   a literal match would wrongly treat a real, present entity as
   unverifiable. Fixed by collapsing whitespace before comparing.

2. `\\b<pattern>\\b` anchoring dropped "Director of Consumer and Business
   Affairs (DCBA)" as unverifiable even though it's genuinely, verbatim
   present, because the phrase ends in ")" and in the real text that ")"
   is followed by "," -- both non-word characters, so \\b finds no
   word/non-word transition there at all. A naive fix (dropping the
   trailing \\b entirely) reopens the false-positive this project already
   guards against ("Acme" matching inside "AcmeCorp"). The actual fix:
   only assert a boundary on a side where the phrase's own edge character
   is itself a word character -- a parenthesis or comma is already
   self-delimiting, \\b's job there was never meaningful. Same shape of
   bug record-scanner's own query_match.py history already worked through
   once, for a different boundary case (a hyphen isn't a \\w character
   either, so \\b\\b reports "cfpb-nominations" present inside
   "cfpb-nominations-2027").

3. A model response wrote "Governor's" with a plain ASCII apostrophe while
   the real source PDF used a Unicode right single quote ("Governor's"),
   confirmed by inspecting the actual bytes. Fixed by normalizing common
   apostrophe/quote variants to their ASCII form before comparing --
   otherwise a real, correctly-extracted entity gets wrongly flagged as
   unverifiable purely over which apostrophe character a PDF's typesetter
   happened to use.
"""

import re

_QUOTE_VARIANTS = str.maketrans({
    "‘": "'", "’": "'",  # left/right single quotation mark
    "“": '"', "”": '"',  # left/right double quotation mark
    "`": "'", "´": "'",  # grave accent, acute accent
})


def normalize_text(s: str) -> str:
    s = (s or "").translate(_QUOTE_VARIANTS)
    return re.sub(r"\s+", " ", s).strip()


def phrase_present(phrase: str, text: str) -> bool:
    phrase = normalize_text(phrase)
    text = normalize_text(text)
    if not phrase or not text:
        return False

    pattern = re.escape(phrase)
    if phrase[0].isalnum() or phrase[0] == "_":
        pattern = r"\b" + pattern
    if phrase[-1].isalnum() or phrase[-1] == "_":
        pattern = pattern + r"\b"

    return re.search(pattern, text, re.IGNORECASE) is not None
