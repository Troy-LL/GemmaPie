from __future__ import annotations

from pathlib import Path
from typing import Mapping

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


def build_context_for(
    agent: str,
    *,
    question: str,
    outputs: Mapping[str, str],
    session_path: Path,
    max_chars: int,
    parallel_initial: bool = False,
    prior_reference: str | None = None,
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
