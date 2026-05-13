"""Tests for model fallback chain parsing (imports pipeline module)."""

from src.pipeline import _parse_model_fallback_chain


def test_fallback_chain_absent() -> None:
    assert _parse_model_fallback_chain({}) == []


def test_fallback_chain_list() -> None:
    cfg = {"model_fallback_chain": ["gemini-2.5-flash", "", " gemini-2.5-flash-lite "]}
    assert _parse_model_fallback_chain(cfg) == [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]


def test_fallback_chain_dict_disabled() -> None:
    cfg = {"model_fallback_chain": {"enabled": False, "models": ["x"]}}
    assert _parse_model_fallback_chain(cfg) == []


def test_fallback_chain_dict_models() -> None:
    cfg = {
        "model_fallback_chain": {
            "enabled": True,
            "models": ["gemini-2.5-flash"],
        }
    }
    assert _parse_model_fallback_chain(cfg) == ["gemini-2.5-flash"]
