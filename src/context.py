from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from . import scratchpad


def trim_to_budget(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 40
    return text[:head] + "\n\n[... context trimmed ...]\n\n" + text[-tail:]


def _facts_block(shared_facts: str, max_chars: int) -> str:
    if not shared_facts.strip():
        return ""
    body = trim_to_budget(shared_facts.strip(), max_chars // 4)
    return f"### Shared facts (merged)\n{body}\n"


def _q(question: str, max_chars: int) -> str:
    return trim_to_budget(question.strip(), min(max_chars // 3, 8000))


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    except OSError:
        return ""


def _norm_exts(exts: Iterable[str] | None) -> set[str]:
    out: set[str] = set()
    for e in exts or (".md", ".txt"):
        s = str(e).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.add(s)
    return out or {".md", ".txt"}


def _iter_kb_files(entry: Path, allowed_exts: set[str]) -> list[Path]:
    if entry.is_file():
        if entry.suffix.lower() in allowed_exts:
            return [entry]
        return []
    if not entry.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(entry.rglob("*")):
        if p.is_file() and p.suffix.lower() in allowed_exts:
            out.append(p)
    return out


def _pretty_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def build_knowledge_reference(
    *,
    root: Path,
    entries: Iterable[str],
    max_chars: int,
    allowed_exts: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """
    Build a bounded context block from user-provided files/folders.

    Returns:
      - markdown/reference block for prompts
      - list of file paths actually included (for session audit)
    """
    if max_chars <= 0:
        return "", []
    exts = _norm_exts(allowed_exts)
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw in entries:
        s = str(raw).strip()
        if not s:
            continue
        p = Path(s).expanduser()
        if not p.is_absolute():
            p = (root / p).resolve()
        files = _iter_kb_files(p, exts)
        for fp in files:
            key = str(fp.resolve()).lower()
            if key not in seen:
                seen.add(key)
                candidates.append(fp)

    if not candidates:
        return "", []

    lines = ["### User-provided knowledge base (reference context)"]
    used: list[str] = []
    # Keep room for headers/footer.
    budget_left = max(0, max_chars - 180)
    for fp in candidates:
        if budget_left <= 120:
            break
        body = _read_text_safe(fp).strip()
        if not body:
            continue
        rel = _pretty_rel(fp, root)
        header = f"\n#### Source: {rel}\n"
        if len(header) >= budget_left:
            break
        budget_left -= len(header)
        piece = body if len(body) <= budget_left else body[: max(0, budget_left - 28)] + "\n...[truncated]"
        lines.append(header + piece)
        used.append(rel)
        budget_left -= len(piece)

    if not used:
        return "", []

    block = "\n".join(lines).strip() + "\n"
    return block, used


def write_knowledge_sources_json(session_path: Path, used_files: list[str]) -> None:
    if not used_files:
        return
    payload = {"used_files": used_files}
    try:
        (session_path / "knowledge_sources.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def build_context_for(
    agent: str,
    *,
    question: str,
    outputs: Mapping[str, str],
    session_path: Path,
    max_chars: int,
    parallel_initial: bool = False,
    prior_reference: str | None = None,
    knowledge_reference: str | None = None,
) -> str:
    """
    Role-based context isolation per implementation plan Phase 4.

    Each agent only sees allowed prior outputs (+ optional shared facts + question).
    """
    agent_l = agent.lower()
    facts = scratchpad.read_shared_facts(session_path)
    fb = _facts_block(facts, max_chars)
    q = _q(question, max_chars)

    def o(name: str) -> str:
        t = outputs.get(name, "")
        return trim_to_budget(t.strip(), max_chars // 3) if t else ""

    parts: list[str] = [f"### User question\n{q}\n"]
    if fb:
        parts.append(fb)

    kb = (knowledge_reference or "").strip()
    if kb:
        parts.append(kb + "\n")

    pr = (prior_reference or "").strip()
    if pr and agent_l == "researcher":
        pr_t = trim_to_budget(pr, max_chars // 4)
        parts.append(
            "### Prior similar-session reference (token reuse / continuity)\n"
            f"{pr_t}\n"
        )

    if agent_l == "researcher":
        parts.append(
            "### Instructions for this turn\n"
            "You only have the question and shared facts. Produce your analysis.\n"
        )
        return trim_to_budget("\n".join(parts), max_chars)

    if agent_l == "skeptic":
        if parallel_initial:
            parts.append(
                "### Parallel mode\n"
                "You are running in parallel with the Researcher. "
                "You do **not** yet have their draft. Critique the question framing, "
                "common biases, and what evidence would be required.\n"
            )
        else:
            r = o("researcher")
            parts.append(f"### Researcher output\n{r}\n")
        parts.append("### Instructions for this turn\nProduce your skeptical critique.\n")
        return trim_to_budget("\n".join(parts), max_chars)

    if agent_l == "contrarian":
        parts.append(f"### Researcher output\n{o('researcher')}\n")
        parts.append(f"### Skeptic output\n{o('skeptic')}\n")
        parts.append("### Instructions for this turn\nSurface the strongest opposing views.\n")
        return trim_to_budget("\n".join(parts), max_chars)

    if agent_l == "reviewer":
        parts.append(f"### Researcher output\n{o('researcher')}\n")
        parts.append(f"### Skeptic output\n{o('skeptic')}\n")
        parts.append(f"### Contrarian output\n{o('contrarian')}\n")
        parts.append("### Instructions for this turn\nReview and compare the three prior agents.\n")
        return trim_to_budget("\n".join(parts), max_chars)

    if agent_l == "synthesizer":
        parts.append(f"### Researcher output\n{o('researcher')}\n")
        parts.append(f"### Skeptic output\n{o('skeptic')}\n")
        parts.append(f"### Contrarian output\n{o('contrarian')}\n")
        parts.append(f"### Reviewer output\n{o('reviewer')}\n")
        if o("skeptic_round2"):
            parts.append(f"### Skeptic (follow-up round) output\n{o('skeptic_round2')}\n")
        if o("researcher_round2"):
            parts.append(f"### Researcher (follow-up round) output\n{o('researcher_round2')}\n")
        parts.append(
            "### Instructions for this turn\n"
            "Integrate into a final answer. Include the required `## Claims` JSON block.\n"
        )
        return trim_to_budget("\n".join(parts), max_chars)

    # Extra round agents reuse researcher/skeptic keys with different stored keys
    if agent_l == "skeptic_round2":
        parts.append(f"### Researcher (initial) output\n{o('researcher')}\n")
        parts.append(f"### Skeptic (initial) output\n{o('skeptic')}\n")
        parts.append(f"### Contrarian output\n{o('contrarian')}\n")
        parts.append(f"### Reviewer output\n{o('reviewer')}\n")
        parts.append("### Disagreement summary\n" + o("disagreement_summary") + "\n")
        parts.append(
            "### Instructions for this turn\n"
            "Follow-up skeptical pass after high inter-agent confidence disagreement.\n"
        )
        return trim_to_budget("\n".join(parts), max_chars)

    if agent_l == "researcher_round2":
        parts.append(f"### Skeptic (follow-up round) output\n{o('skeptic_round2')}\n")
        parts.append(f"### Your prior Researcher output\n{o('researcher')}\n")
        parts.append(f"### Skeptic (initial) output\n{o('skeptic')}\n")
        parts.append(f"### Contrarian output\n{o('contrarian')}\n")
        parts.append(f"### Reviewer output\n{o('reviewer')}\n")
        parts.append("### Disagreement summary\n" + o("disagreement_summary") + "\n")
        parts.append(
            "### Instructions for this turn\n"
            "Revise or extend your analysis; respond to the follow-up Skeptic and the disagreement summary.\n"
        )
        return trim_to_budget("\n".join(parts), max_chars)

    parts.append("### Generic context\nUnknown agent role; include all prior outputs.\n")
    for k in ("researcher", "skeptic", "contrarian", "reviewer"):
        parts.append(f"### {k}\n{o(k)}\n")
    return trim_to_budget("\n".join(parts), max_chars)
