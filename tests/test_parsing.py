"""Tests for src/parsing.py (no Gemini)."""

from src.parsing import parse_response_tail, split_thinking_body


def test_split_thinking_tag_preferred() -> None:
    text = "<thinking>\nstep\n</thinking>\nPublic answer.\nConfidence: 5/10\nKey uncertainty: x"
    sp = split_thinking_body(text)
    assert "step" in sp.thinking
    assert "Public answer" in sp.public
    assert "<thinking>" not in sp.public


def test_split_scratch_reasoning() -> None:
    text = "## Scratch reasoning\nscratch line\n\nConfidence: 4/10\nKey uncertainty: y\n"
    sp = split_thinking_body(text)
    assert sp.thinking
    assert "Confidence" in sp.public or "4/10" in sp.public


def test_split_no_thinking_all_public() -> None:
    text = "Just output.\nConfidence: 7/10\nKey uncertainty: z"
    sp = split_thinking_body(text)
    assert sp.thinking == ""
    assert "Just output" in sp.public


def test_parse_response_tail() -> None:
    tail = parse_response_tail("...\nConfidence: 8/10\nKey uncertainty: drought risk\n")
    assert tail.confidence == 8
    assert tail.uncertainty and "drought" in tail.uncertainty.lower()
