"""Tests for src/context.py KB helpers (tmp fixtures)."""

from pathlib import Path

from src.context import build_knowledge_reference, trim_to_budget


def test_trim_to_budget_short_unchanged() -> None:
    assert trim_to_budget("hello", 100) == "hello"


def test_trim_to_budget_long_has_marker() -> None:
    long_text = "x" * 500
    # head/tail split needs room beyond marker; use max_chars so tail slice is non-empty.
    out = trim_to_budget(long_text, max_chars=120)
    assert "[... context trimmed ...]" in out
    assert len(out) <= 120 + 60


def test_build_knowledge_reference_two_files(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.md").write_text("Alpha content.", encoding="utf-8")
    (root / "b.md").write_text("Beta content.", encoding="utf-8")
    result = build_knowledge_reference(
        root=root,
        entries=[str(root / "a.md"), str(root / "b.md")],
        max_chars=5000,
        allowed_exts=[".md"],
    )
    assert "User-provided knowledge base" in result.block
    assert "Alpha" in result.block or "Beta" in result.block
    assert len(result.used_files) == 2


def test_build_knowledge_reference_budget_trims(tmp_path: Path) -> None:
    root = tmp_path
    (root / "big.md").write_text("Z" * 4000, encoding="utf-8")
    # budget_left must stay > 120 after header reservation (see context.build_knowledge_reference).
    result = build_knowledge_reference(
        root=root,
        entries=[str(root / "big.md")],
        max_chars=900,
        allowed_exts=[".md"],
    )
    assert result.used_files == ["big.md"]
    assert "truncated" in result.block.lower() or len(result.block) < 4200
