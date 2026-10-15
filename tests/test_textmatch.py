from src.textmatch import normalize_text, phrase_present


def test_finds_a_plain_phrase():
    assert phrase_present("Acme Construction", "The city approved Acme Construction's bid.")


def test_does_not_find_a_phrase_that_is_absent():
    assert not phrase_present("Acme Construction", "Nothing relevant here.")


def test_rejects_a_substring_of_a_longer_word():
    # "Acme" must not match inside "AcmeCorp" -- the false positive this
    # project already knows to guard against.
    assert not phrase_present("Acme", "AcmeCorp announced a merger.")


def test_tolerates_a_pdf_line_wrap_in_the_middle_of_a_phrase():
    # Real case: pypdf extracted "Department of Parks \nand Recreation"
    # from a real LA County agenda PDF.
    source = "allocated to the Department of Parks \nand Recreation for the project."
    assert phrase_present("Department of Parks and Recreation", source)


def test_finds_a_phrase_ending_in_a_closing_parenthesis():
    # Real case, live 2026-08-31: naive \b\b anchoring rejected this exact
    # phrase because ")" followed by "," has no word/non-word transition.
    source = "instruct the Director of Consumer and Business Affairs (DCBA), in collaboration"
    assert phrase_present("Director of Consumer and Business Affairs (DCBA)", source)


def test_parenthetical_phrase_still_requires_the_whole_thing_present():
    source = "the Director of Consumer and Business Affairs did something else entirely"
    assert not phrase_present("Director of Consumer and Business Affairs (DCBA)", source)


def test_case_insensitive():
    assert phrase_present("acme construction", "Approved by ACME CONSTRUCTION today.")


def test_empty_phrase_or_text_is_never_present():
    assert not phrase_present("", "some text")
    assert not phrase_present("Acme", "")
    assert not phrase_present(None, "some text")


def test_normalize_text_collapses_newlines_and_multiple_spaces():
    assert normalize_text("a  b\nc\t d") == "a b c d"


def test_normalize_text_handles_none():
    assert normalize_text(None) == ""


def test_finds_a_phrase_when_the_source_uses_a_unicode_smart_apostrophe():
    # Real case, live 2026-08-31: a real captured PrimeGov committee
    # agenda used "Governor’s" (U+2019 right single quote) while the
    # model's own extracted entity used a plain ASCII apostrophe. Confirmed
    # by inspecting the actual PDF-extracted bytes, not assumed.
    source = "the California Governor’s Office of Emergency Services (Cal OES) agreement"
    assert phrase_present("California Governor's Office of Emergency Services (Cal OES)", source)


def test_finds_a_phrase_when_the_query_itself_uses_the_smart_apostrophe():
    source = "the California Governor's Office of Emergency Services"
    assert phrase_present("California Governor’s Office of Emergency Services", source)


def test_normalize_text_collapses_smart_quote_variants():
    assert normalize_text("‘a’ “b”") == "'a' \"b\""


def test_normalize_text_strips_leading_and_trailing_whitespace():
    assert normalize_text("  a b \n\n") == "a b"
