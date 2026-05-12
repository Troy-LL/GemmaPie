"""Find prior sessions with similar questions for reuse (inject context or short-circuit)."""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple


# Light English stopword list so overlap focuses on content words (not "is the a").
_STOP = frozenset(
    """
    a an the and or but if as at by for from in into is it its of on to with
    was were be been being are am i you we they he she it this that these those
    what which who whom whose where when why how about than then do does did
    can could should would will just only not no yes also too very more most
    some any each every both few such same other another
    """.split()
)


class PriorSessionMatch(NamedTuple):
    """Result of scanning for a reusable prior session."""

    session_dir: Path | None
    char_similarity: float
    prior_question: str
    word_overlap: float
    age_days: float | None


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


def word_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", normalize_question(text))
    return {w for w in words if len(w) >= 2 and w not in _STOP}


def word_overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap on content words (complements raw character similarity)."""
    A, B = word_tokens(a), word_tokens(b)
    if not A or not B:
        return 0.0
    inter = A & B
    union = A | B
    return len(inter) / len(union) if union else 0.0


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


def session_age_days(session_dir: Path, *, now: float | None = None) -> float | None:
    """Return age of the session folder in days (mtime), or None if unknown."""
    t0 = time.time() if now is None else float(now)
    try:
        mt = session_dir.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (t0 - mt) / 86400.0)


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
    min_word_overlap: float = 0.0,
    max_session_age_days: float | None = None,
) -> PriorSessionMatch:
    """
    Pick the best prior session that passes **all** gates:

    - Character similarity >= ``similarity_threshold``
    - If ``min_word_overlap`` > 0: content-word Jaccard >= that value
    - If ``max_session_age_days`` is set: session mtime must be newer than that limit
    """
    best: tuple[Path, float, str, float, float] | None = None
    best_key: tuple[float, float] = (-1.0, -1.0)
    ex = exclude_session.resolve() if exclude_session else None
    now = time.time()

    for p in _session_dirs_newest_first(sessions_root):
        if ex is not None and p.resolve() == ex:
            continue
        age = session_age_days(p, now=now)
        if max_session_age_days is not None and age is not None:
            if age > float(max_session_age_days):
                continue
        pq = read_session_question(p)
        char_sim = similarity_ratio(question, pq)
        if char_sim < similarity_threshold:
            continue
        word_sim = word_overlap_ratio(question, pq)
        if min_word_overlap > 0.0 and word_sim < float(min_word_overlap):
            continue
        key = (char_sim, word_sim)
        if key > best_key:
            best_key = key
            best = (p, char_sim, pq, word_sim, age if age is not None else 0.0)

    if best is None:
        return PriorSessionMatch(None, 0.0, "", 0.0, None)
    p, char_sim, pq, word_sim, age_d = best
    return PriorSessionMatch(p, char_sim, pq, word_sim, age_d)


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
    word_overlap: float,
    max_chars: int,
) -> str:
    pq = trim_prior_block(prior_question.strip(), min(1200, max_chars // 4))
    pa = trim_prior_block(prior_answer.strip(), max_chars)
    return (
        "### Read this first (GemmaPie session reuse)\n\n"
        "- Matching uses **similar wording**, not true “understanding.” The same words can mean "
        "different things; different words can mean the same thing.\n"
        "- The text below is from an **older run** and may be **out of date** (facts, laws, numbers, or context).\n"
        "- Use it only as **background**. Compare to the **current** user question and produce a **fresh** analysis.\n\n"
        f"**Matched prior session:** `{prior_session.name}` "
        f"(wording similarity ~{similarity:.0%}; shared important words ~{word_overlap:.0%})\n\n"
        f"**Prior question (excerpt):**\n{pq}\n\n"
        f"**Prior integrated answer (reference only):**\n{pa}\n\n"
        "**Your job:** Validate against the current question; update or replace the prior conclusion when needed."
    )


def append_topic_log(
    sessions_root: Path,
    *,
    session_id: str,
    norm_question: str,
    reuse_mode: str,
    matched_session: str | None,
    similarity: float | None,
    **extra: object,
) -> None:
    """Append one JSON line for lightweight tracking (no embeddings)."""
    try:
        sessions_root.mkdir(parents=True, exist_ok=True)
        log_path = sessions_root / "topic_log.jsonl"
        line: dict[str, object] = {
            "session": session_id,
            "norm_question": norm_question[:500],
            "reuse_mode": reuse_mode,
            "matched_session": matched_session,
            "similarity": similarity,
        }
        for k, v in extra.items():
            if v is not None:
                line[k] = v
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
