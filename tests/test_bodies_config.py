"""bodies.yaml's inactive template entries (legistar, granicus) are never
exercised by scan.py -- it filters to active bodies before ever calling
build_adapter(). A typo in one of those templates would sit unnoticed until
someone flipped active: true and it broke. This constructs every entry's
adapter (not .discover(), which would hit the network) to catch that class
of config error offline.
"""

from src.adapters import build_adapter
from src.scan import load_bodies


def test_bodies_yaml_parses():
    bodies = load_bodies()
    assert len(bodies) >= 5  # 3 pilot bodies + 2 inactive templates


def test_every_body_including_inactive_templates_builds_a_valid_adapter():
    for body in load_bodies():
        adapter = build_adapter(
            body["source_type"], body.get("source_config", {}), http_get=lambda *a, **k: None
        )
        assert adapter.source_config == body.get("source_config", {})


def test_pilot_bodies_are_active_and_the_templates_are_not():
    bodies = {b["name"]: b for b in load_bodies()}
    assert bodies["Los Angeles City Council"]["active"] is True
    assert bodies["Los Angeles County Board of Supervisors"]["active"] is True
    assert bodies["Los Angeles Board of Police Commissioners"]["active"] is True
    assert bodies["Example Legistar City Council"]["active"] is False
    assert bodies["Example Granicus Client"]["active"] is False


def test_unknown_source_type_raises_a_clear_error():
    try:
        build_adapter("not_a_real_type", {}, http_get=None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not_a_real_type" in str(e)
