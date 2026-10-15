"""scan.py's main() loop, proven against a body whose adapter genuinely
raises. Found while reviewing the code, not hypothetical: pdf_directory's
LAPD config is a hardcoded yearly URL that will start 404ing at the next
calendar year boundary until bodies.yaml is updated, and nothing upstream
of this fix caught that kind of per-body failure -- it would have taken the
whole day's run down with it, every body after the failing one in
bodies.yaml never scanned. Matches this project's own stated rule (see
record-scanner's run.sh): one lane's outage must not cost the whole run.
"""

import sys

import src.scan as scan_mod


class FakeAdapter:
    def __init__(self, source_config, http_get, should_raise=False):
        self.should_raise = should_raise

    def discover(self):
        if self.should_raise:
            raise RuntimeError("simulated: index page returned 404")
        return iter([])


def test_one_body_raising_does_not_stop_the_others(monkeypatch, capsys):
    bodies = [
        {"name": "Broken Body", "jurisdiction": "X", "source_type": "pdf_directory",
         "active": True, "source_config": {}},
        {"name": "Working Body", "jurisdiction": "Y", "source_type": "pdf_directory",
         "active": True, "source_config": {}},
    ]
    monkeypatch.setattr(scan_mod, "load_bodies", lambda: bodies)
    monkeypatch.setattr(scan_mod, "load_dotenv", lambda: None)

    def fake_build_adapter(source_type, source_config, http_get):
        # only the first call (Broken Body) raises
        should_raise = not fake_build_adapter.called
        fake_build_adapter.called = True
        return FakeAdapter(source_config, http_get, should_raise=should_raise)

    fake_build_adapter.called = False
    monkeypatch.setattr(scan_mod, "build_adapter", fake_build_adapter)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run"])

    scan_mod.main()  # must not raise

    captured = capsys.readouterr()
    assert "Broken Body" in captured.out
    assert "Working Body" in captured.out
    assert "body crashed" in captured.err


def test_a_crashed_body_counts_as_an_error_in_totals(monkeypatch, capsys):
    bodies = [
        {"name": "Broken Body", "jurisdiction": "X", "source_type": "pdf_directory",
         "active": True, "source_config": {}},
    ]
    monkeypatch.setattr(scan_mod, "load_bodies", lambda: bodies)
    monkeypatch.setattr(scan_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        scan_mod, "build_adapter",
        lambda source_type, source_config, http_get: FakeAdapter(source_config, http_get, should_raise=True),
    )
    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run"])

    scan_mod.main()

    out = capsys.readouterr().out
    assert "TOTAL" in out
    assert "errors=1" in out
