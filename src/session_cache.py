"""Find prior sessions with similar questions for reuse (inject context or short-circuit)."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path


def normalize_question(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def similarity_ratio(a: str, b: str) -> float:
    x, y = normalize_question(a), normalize_question(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    return float(SequenceMatcher(None, x, y).ratio())


def _session_dirs_newest_first(sessions_root: Path) -> list[Path]:
    if not sessions_root.is_dir():
        return []
    scored: list[tuple[float, Path]] = []
    for p in sessions_root.iterdir():
        if not p.is_dir() or not p.name.startswith("session_"):
            continue
        if not (p / "question.txt").is_file():
            continue
        try:
            scored.append((p.stat().st_mtime, p))
        except OSError:
            continue
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored]


def read_session_question(session_dir: Path) -> str:
    try:
        return (session_dir / "question.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def extract_body_from_final_answer_banner(text: str) -> str:
    """Strip our FINAL ANSWER banner; return narrative or full text."""
    t = (text or "").strip()
    m = re.search(
        r"Question:\s*[^\n]*\n\n([\s\S]+?)(?:\n\n={10,}|\Z)",
        t,
    )
    if m:
        return m.group(1).strip()
    return t


def read_prior_integrated_answer(session_dir: Path) -> str | None:
    """
    Prefer compact `synthesizer_public.txt`; else parse `final_answer.txt`;
    else scratchpad `## synthesizer` section.
    """
    pub = session_dir / "synthesizer_public.txt"
    if pub.is_file():
        try:
            s = pub.read_text(encoding="utf-8").strip()
            if s and not s.startswith("(synthesizer failed"):
                return s
        except OSError:
            pass
    fa = session_dir / "final_answer.txt"
    if fa.is_file():
        try:
            raw = fa.read_text(encoding="utf-8").strip()
            if raw:
                body = extract_body_from_final_answer_banner(raw)
                if body:
                    return body
        except OSError:
            pass
    sp = session_dir / "scratchpad.md"
    if sp.is_file():
        try:
            text = sp.read_text(encoding="utf-8")
        except OSError:
            return None
        m = re.search(
            r"##\s+synthesizer[^\n]*\n\n([\s\S]*?)(?=\n## |\n---\s*\n|\Z)",
            text,
            re.IGNORECASE,
        )
        if m:
            chunk = m.group(1).strip()
            if chunk and not chunk.startswith("(synthesizer failed"):
                return chunk
    return None


def find_best_prior_session(
    *,
    sessions_root: Path,
    question: str,
    exclude_session: Path | None,
    similarity_threshold: float,
) -> tuple[Path | None, float, str]:
    """
    Return (best_session_dir, similarity, prior_question) for sessions at or above threshold.
    Newest sessions are checked first among equal scores.
    """
    best_path: Path | None = None
    best_score = 0.0
    best_pq = ""
    ex = exclude_session.resolve() if exclude_session else None
    for p in _session_dirs_newest_first(sessions_root):
        if ex is not None and p.resolve() == ex:
            continue
        pq = read_session_question(p)
        sim = similarity_ratio(question, pq)
        if sim >= similarity_threshold and sim >= best_score:
            best_score = sim
            best_path = p
            best_pq = pq
    return best_path, best_score, best_pq


def trim_prior_block(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = max_chars - 80
    return text[:head].rstrip() + "\n\n[... prior answer truncated for context budget ...]"


def format_prior_reference_block(
    *,
    prior_session: Path,
    prior_question: str,
    prior_answer: str,
    similarity: float,
    max_chars: int,
) -> str:
    pq = trim_prior_block(prior_question.strip(), min(1200, max_chars // 4))
    pa = trim_prior_block(prior_answer.strip(), max_chars)
    return (
        f"**Matched prior session:** `{prior_session.name}` (question similarity ~{similarity:.0%})\n\n"
        f"**Prior question (excerpt):**\n{pq}\n\n"
        f"**Prior integrated answer (reference; may be outdated):**\n{pa}\n\n"
        "**Your job:** Use this only as background. Compare carefully to the **current** user question, "
        "check whether the prior conclusion still applies, and produce a fresh analysis where needed."
    )


def append_topic_log(
    sessions_root: Path,
    *,
    session_id: str,
    norm_question: str,
    reuse_mode: str,
    matched_session: str | None,
    similarity: float | None,
) -> None:
    """Append one JSON line for lightweight tracking (no embeddings)."""
    try:
        sessions_root.mkdir(parents=True, exist_ok=True)
        log_path = sessions_root / "topic_log.jsonl"
        line = {
            "session": session_id,
            "norm_question": norm_question[:500],
            "reuse_mode": reuse_mode,
            "matched_session": matched_session,
            "similarity": similarity,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
