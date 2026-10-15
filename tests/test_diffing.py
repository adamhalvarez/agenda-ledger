from src.diffing import plain_language_summary, unified_diff


def test_unified_diff_shows_added_line():
    old = "Item 1: Approve contract\nItem 2: Receive and file\n"
    new = "Item 1: Approve contract\nItem 2: Receive and file\nItem 3: Closed session\n"
    diff = unified_diff(old, new)
    assert "+Item 3: Closed session" in diff
    assert "-Item 3" not in diff


def test_unified_diff_shows_removed_line():
    old = "Item 1: Approve contract\nItem 2: Receive and file\n"
    new = "Item 1: Approve contract\n"
    diff = unified_diff(old, new)
    assert "-Item 2: Receive and file" in diff


def test_unified_diff_empty_when_unchanged():
    text = "Item 1: Approve contract\n"
    assert unified_diff(text, text) == ""


def test_summary_reports_no_difference():
    text = "Item 1: Approve contract\n"
    assert plain_language_summary(text, text) == "No textual difference from the previous version."


def test_summary_counts_added_and_removed_lines():
    old = "Item 1: Approve contract\nItem 2: Receive and file\n"
    new = "Item 1: Approve contract\nItem 2: Receive and file\nItem 3: Closed session\n"
    summary = plain_language_summary(old, new)
    assert "1 line(s) added, 0 line(s) removed" in summary


def test_summary_names_a_real_changed_line_verbatim():
    old = "Item 1: Approve contract for $50,000\n"
    new = "Item 1: Approve contract for $75,000\n"
    summary = plain_language_summary(old, new)
    assert "$75,000" in summary


def test_summary_handles_none_text():
    assert plain_language_summary(None, "Item 1: New agenda\n") == (
        "1 line(s) added, 0 line(s) removed compared to the previous version. "
        'Includes a change near: "Item 1: New agenda"'
    )
