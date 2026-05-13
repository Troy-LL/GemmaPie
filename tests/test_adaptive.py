"""Tests for src/adaptive.py (no Gemini for pure helpers)."""

from src import adaptive


def test_parse_trivial_add_match() -> None:
    assert adaptive.parse_trivial_add("3 + 5", max_digits=6) == (3, 5, 8)
    assert adaptive.parse_trivial_add("  12 + 34  ", max_digits=6) == (12, 34, 46)


def test_parse_trivial_add_no_match() -> None:
    assert adaptive.parse_trivial_add("what is 2+2", max_digits=6) is None


def test_classify_heuristic_t0() -> None:
    r = adaptive.classify_heuristic("1+1", max_digits=6)
    assert r.tier == "T0"
    assert "1+1" in r.reason or "trivial" in r.reason.lower()


def test_classify_heuristic_unknown() -> None:
    r = adaptive.classify_heuristic("Is nuclear energy safe?", max_digits=6)
    assert r.tier == "unknown"


def test_extract_json_object_raw() -> None:
    assert adaptive._extract_json_object('{"tier":"T2","reason":"x"}') == {
        "tier": "T2",
        "reason": "x",
    }


def test_extract_json_object_fence() -> None:
    text = 'Here:\n```json\n{"tier":"T1","reason":"narrow"}\n```'
    assert adaptive._extract_json_object(text) == {"tier": "T1", "reason": "narrow"}
