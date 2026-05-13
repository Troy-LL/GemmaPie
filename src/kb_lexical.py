"""Lexical KB v2: chunk, dedupe, TF-IDF-style ranking vs question, pack to char budget."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ATX_HEADING = re.compile(r"^(#{1,6}\s+.+)$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass
class KbChunk:
    """One passage unit from a KB file."""

    chunk_id: str
    rel_path: str
    chunk_index: int
    body: str
    fingerprint: str = ""
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = fingerprint_text(self.body)


def fingerprint_text(text: str) -> str:
    s = " ".join((text or "").split()).strip().lower()
    return s


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "") if len(m.group(0)) >= 2]


def _hard_split(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []
    out: list[str] = []
    i = 0
    while i < len(text):
        piece = text[i : i + max_chars]
        if i + max_chars < len(text):
            piece = piece.rstrip() + "\n...[split]...\n"
        out.append(piece)
        i += max_chars
    return out


def _split_paragraphs(text: str, max_chunk_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"\n\s*\n+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chunk_chars:
            out.append(p)
        else:
            out.extend(_hard_split(p, max_chunk_chars))
    return out if out else _hard_split(text, max_chunk_chars)


def chunk_markdown(rel_path: str, raw: str, max_chunk_chars: int) -> list[str]:
    """Split .md on ATX headings; each chunk starts with its heading line if any."""
    raw = (raw or "").strip()
    if not raw:
        return []
    lines = raw.splitlines()
    if not any(_ATX_HEADING.match(line.strip()) for line in lines):
        return _split_paragraphs(raw, max_chunk_chars)

    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _ATX_HEADING.match(stripped) and current:
            body = "\n".join(current).strip()
            if body:
                for piece in _hard_split(body, max_chunk_chars) if len(body) > max_chunk_chars else [body]:
                    chunks.append(piece)
            current = [line]
        else:
            current.append(line)
    if current:
        body = "\n".join(current).strip()
        if body:
            for piece in _hard_split(body, max_chunk_chars) if len(body) > max_chunk_chars else [body]:
                chunks.append(piece)
    return chunks


def chunk_plain_text(rel_path: str, raw: str, max_chunk_chars: int) -> list[str]:
    return _split_paragraphs(raw, max_chunk_chars)


def file_to_chunks(rel_path: str, suffix: str, raw: str, max_chunk_chars: int) -> list[str]:
    suf = suffix.lower()
    if suf == ".md":
        return chunk_markdown(rel_path, raw, max_chunk_chars)
    return chunk_plain_text(rel_path, raw, max_chunk_chars)


def build_chunks_for_file(rel_path: str, suffix: str, raw: str, max_chunk_chars: int) -> list[KbChunk]:
    pieces = file_to_chunks(rel_path, suffix, raw, max_chunk_chars)
    out: list[KbChunk] = []
    for i, body in enumerate(pieces):
        cid = f"{rel_path.replace(chr(92), '/')}#c{i}"
        out.append(KbChunk(chunk_id=cid, rel_path=rel_path, chunk_index=i, body=body.strip()))
    return out


def dedupe_chunks(chunks: list[KbChunk]) -> tuple[list[KbChunk], int]:
    """First-seen wins by fingerprint; later duplicates are dropped."""
    seen: set[str] = set()
    kept: list[KbChunk] = []
    dropped = 0
    for ch in chunks:
        fp = ch.fingerprint
        if not fp:
            kept.append(ch)
            continue
        if fp in seen:
            dropped += 1
            continue
        seen.add(fp)
        kept.append(ch)
    return kept, dropped


def score_chunks_tf_idf(question: str, chunks: list[KbChunk]) -> None:
    q_terms = tokenize(question)
    if not q_terms or not chunks:
        for c in chunks:
            c.score = 0.0
        return
    tfs: list[dict[str, int]] = []
    term_sets: list[set[str]] = []
    for ch in chunks:
        terms = tokenize(ch.body)
        freq: dict[str, int] = {}
        for t in terms:
            freq[t] = freq.get(t, 0) + 1
        tfs.append(freq)
        term_sets.append(set(freq.keys()))
    df: dict[str, int] = {}
    for ts in term_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    n = len(chunks)
    q_set = set(q_terms)
    for i, ch in enumerate(chunks):
        freq = tfs[i]
        s = 0.0
        for t in q_set:
            if t in freq:
                idf = math.log((n + 1.0) / (df.get(t, n) + 1.0)) + 1.0
                s += float(freq[t]) * idf
        ch.score = s


def pack_chunks_to_markdown(
    chunks: list[KbChunk],
    *,
    max_chars: int,
    min_score: float,
    header_budget: int = 180,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """
    Sort by (-score, rel_path, chunk_index), greedily pack.

    Returns (markdown_block_without_top_section, used_files, chunk_records for JSON).
    """
    if max_chars <= 0:
        return "", [], []

    eligible = [c for c in chunks if c.score >= min_score]
    eligible.sort(key=lambda c: (-c.score, c.rel_path.lower(), c.chunk_index))

    lines: list[str] = []
    used_paths: list[str] = []
    seen_path: set[str] = set()
    records: list[dict[str, Any]] = []
    budget = max(0, max_chars - header_budget)

    for ch in eligible:
        header = f"\n#### Source: {ch.rel_path} [`{ch.chunk_id}`]\n"
        body = ch.body
        need = len(header) + len(body) + 1
        if need > budget:
            room = budget - len(header) - 28
            if room < 80:
                continue
            body = body[: max(0, room)] + "\n...[truncated]"
        piece = header + body
        if len(piece) > budget:
            continue
        lines.append(piece)
        records.append({"id": ch.chunk_id, "path": ch.rel_path, "score": round(ch.score, 6)})
        if ch.rel_path not in seen_path:
            seen_path.add(ch.rel_path)
            used_paths.append(ch.rel_path)
        budget -= len(piece)

    inner = "\n".join(lines).strip()
    return inner, used_paths, records


def build_lexical_kb(
    *,
    file_paths: list[Path],
    rel_fn: Any,
    question: str,
    max_chars: int,
    max_chunk_chars: int,
    min_score: float,
    max_chunks_preprocess: int,
    dedupe: bool,
) -> tuple[str, list[str], dict[str, Any]]:
    """
    Load files, chunk, optional dedupe, score, pack.

    rel_fn: (Path) -> str relative path for display.
    """
    all_chunks: list[KbChunk] = []
    for fp in file_paths:
        rel = rel_fn(fp)
        try:
            raw = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            try:
                raw = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
        raw = raw.strip()
        if not raw:
            continue
        new_chunks = build_chunks_for_file(rel, fp.suffix, raw, max_chunk_chars)
        all_chunks.extend(new_chunks)

    dedupe_dropped = 0
    if dedupe:
        all_chunks, dedupe_dropped = dedupe_chunks(all_chunks)

    if max_chunks_preprocess > 0 and len(all_chunks) > max_chunks_preprocess:
        all_chunks = all_chunks[:max_chunks_preprocess]

    score_chunks_tf_idf(question, all_chunks)

    inner, used_files, chunk_records = pack_chunks_to_markdown(
        all_chunks,
        max_chars=max_chars,
        min_score=min_score,
    )

    if not inner:
        meta: dict[str, Any] = {
            "used_files": [],
            "chunks": [],
            "dedupe_dropped": dedupe_dropped,
            "mode": "lexical_v2",
        }
        return "", [], meta

    block = "### User-provided knowledge base (reference context)\n" + inner + "\n"
    meta = {
        "used_files": used_files,
        "chunks": chunk_records,
        "dedupe_dropped": dedupe_dropped,
        "mode": "lexical_v2",
    }
    return block, used_files, meta
