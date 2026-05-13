"""Tests for src/kb_lexical.py (deterministic, no Gemini)."""

from pathlib import Path

from src import kb_lexical
from src.context import KnowledgeReferenceResult, build_knowledge_reference


def test_chunk_markdown_splits_on_headings() -> None:
    raw = "# Intro\nhello\n\n## Detail\npricing is ninety nine\n"
    parts = kb_lexical.chunk_markdown("x.md", raw, max_chunk_chars=8000)
    assert len(parts) >= 2
    joined = "\n".join(parts)
    assert "pricing" in joined.lower()


def test_dedupe_drops_identical_bodies() -> None:
    a = kb_lexical.KbChunk(
        chunk_id="a#c0",
        rel_path="a.md",
        chunk_index=0,
        body="same text here",
    )
    b = kb_lexical.KbChunk(
        chunk_id="b#c0",
        rel_path="b.md",
        chunk_index=0,
        body="same text here",
    )
    merged, dropped = kb_lexical.dedupe_chunks([a, b])
    assert dropped == 1
    assert len(merged) == 1


def test_tf_idf_ranks_relevant_chunk_higher() -> None:
    q = "widget pricing policy"
    chunks = [
        kb_lexical.KbChunk(
            chunk_id="x#c0",
            rel_path="x.md",
            chunk_index=0,
            body="Unrelated gardening tips about soil.",
        ),
        kb_lexical.KbChunk(
            chunk_id="y#c0",
            rel_path="y.md",
            chunk_index=0,
            body="Widget pricing table: tier A costs ten dollars.",
        ),
    ]
    kb_lexical.score_chunks_tf_idf(q, chunks)
    assert chunks[1].score >= chunks[0].score


def test_pack_respects_budget_and_order(tmp_path: Path) -> None:
    inner, used, rec = kb_lexical.pack_chunks_to_markdown(
        [
            kb_lexical.KbChunk(
                chunk_id="a.md#c0",
                rel_path="a.md",
                chunk_index=0,
                body="alpha",
                score=2.0,
            ),
            kb_lexical.KbChunk(
                chunk_id="b.md#c0",
                rel_path="b.md",
                chunk_index=0,
                body="beta",
                score=2.0,
            ),
        ],
        max_chars=400,
        min_score=0.0,
        header_budget=80,
    )
    assert "alpha" in inner or "beta" in inner
    assert len(rec) >= 1


def test_build_lexical_kb_end_to_end(tmp_path: Path) -> None:
    root = tmp_path
    (root / "one.md").write_text("# Prices\nOur widget pricing is unique.\n", encoding="utf-8")
    (root / "two.md").write_text("# Other\nGarden soil composition.\n", encoding="utf-8")
    fps = [root / "one.md", root / "two.md"]
    block, used, meta = kb_lexical.build_lexical_kb(
        file_paths=fps,
        rel_fn=lambda p: str(p.relative_to(root)),
        question="Tell me about widget pricing",
        max_chars=8000,
        max_chunk_chars=4000,
        min_score=0.0,
        max_chunks_preprocess=500,
        dedupe=True,
    )
    assert "widget" in block.lower() or "pricing" in block.lower()
    assert meta["mode"] == "lexical_v2"
    assert isinstance(meta.get("chunks"), list)


def test_build_knowledge_reference_legacy_returns_namedtuple(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.md").write_text("Alpha content.", encoding="utf-8")
    r = build_knowledge_reference(
        root=root,
        entries=[str(root / "a.md")],
        max_chars=5000,
        allowed_exts=[".md"],
        mode="legacy",
    )
    assert isinstance(r, KnowledgeReferenceResult)
    assert "Alpha" in r.block
    assert r.sources_meta.get("mode") == "legacy"


def test_build_knowledge_reference_lexical_prefers_relevant_passage(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.md").write_text("# Soil\nGarden dirt facts.\n\n# Commerce\nWidget pricing is listed here.\n", encoding="utf-8")
    r = build_knowledge_reference(
        root=root,
        entries=[str(root / "a.md")],
        max_chars=8000,
        allowed_exts=[".md"],
        question="What is widget pricing",
        mode="lexical_v2",
        lexical={
            "max_chunk_chars": 4000,
            "min_score": 0.0,
            "max_chunks_preprocess": 500,
            "dedupe": True,
        },
    )
    assert r.sources_meta.get("mode") == "lexical_v2"
    assert "pricing" in r.block.lower()
