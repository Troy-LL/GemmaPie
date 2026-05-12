"""Session transparency artifacts: Markdown report + audit.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .parsing import extract_claims_json, parse_response_tail


def _confidence_range(scores: dict[str, int | None], agents: list[str]) -> str | None:
    vals: list[int] = []
    for a in agents:
        v = scores.get(a)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return f"{min(vals)}-{max(vals)}"


def epistemic_status(
    *,
    disagreement: dict[str, Any],
    scores: dict[str, int | None],
    errors: list[str],
) -> str:
    if errors:
        return "Uncertain"
    confs = [v for v in scores.values() if v is not None]
    if len(confs) < 2:
        return "Uncertain"
    spread = max(confs) - min(confs)
    if disagreement.get("high_disagreement") or spread > 3:
        return "Contested"
    if spread <= 1 and len(errors) == 0:
        return "High Confidence"
    return "Contested"


def build_audit(
    *,
    synthesizer_text: str,
    scores: dict[str, int | None],
) -> dict[str, Any]:
    claims = extract_claims_json(synthesizer_text) or []
    enriched: list[dict[str, Any]] = []
    for c in claims:
        text = str(c.get("claim") or c.get("text", "")).strip()
        if not text:
            continue
        agents = c.get("supporting_agents") or c.get("supportingAgents") or []
        if isinstance(agents, str):
            agents = [agents]
        agents_l = [str(a).lower() for a in agents if str(a).strip()]
        basis = c.get("basis")
        status = c.get("status")
        enriched.append(
            {
                "text": text,
                "status": status,
                "basis": basis,
                "supporting_agents": agents_l,
                "confidence_range": _confidence_range(scores, agents_l),
                "confidence_note": c.get("confidence_note") or c.get("confidenceNote"),
            }
        )
    return {"claims": enriched}


def write_report(
    *,
    session_path: Path,
    question: str,
    outputs: dict[str, str],
    disagreement: dict[str, Any],
    scores: dict[str, int | None],
    synthesizer_confidence: int | None,
    errors: list[str],
    models_used: dict[str, Any] | None = None,
    thinking_outputs: dict[str, str] | None = None,
    show_thinking: bool = False,
) -> None:
    status = epistemic_status(disagreement=disagreement, scores=scores, errors=errors)
    lines: list[str] = [
        "# Transparency report",
        "",
        f"**Epistemic status:** {status}",
        "",
        "## Original question",
        "",
        question.strip(),
        "",
        "## Agent responses",
        "",
    ]
    order = [
        "researcher",
        "skeptic",
        "contrarian",
        "reviewer",
        "skeptic_round2",
        "researcher_round2",
        "synthesizer",
    ]
    for key in order:
        if key not in outputs:
            continue
        body = outputs[key].strip()
        tail = parse_response_tail(body)
        lines.append(f"### {key}")
        lines.append("")
        if models_used:
            mid = models_used.get(key) or models_used.get("default")
            if mid:
                lines.append(f"_Model (`-m`): `{mid}`_")
                lines.append("")
        lines.append(body)
        lines.append("")
        if show_thinking and thinking_outputs and thinking_outputs.get(key):
            lines.append("#### Thinking trace")
            lines.append("")
            lines.append(thinking_outputs[key].strip())
            lines.append("")
        if tail.confidence is not None:
            lines.append(f"_Parsed confidence: {tail.confidence}/10_")
            lines.append("")

    lines.extend(
        [
            "## Disagreement analysis",
            "",
            f"- High disagreement: **{disagreement.get('high_disagreement')}**",
            f"- Max spread: **{disagreement.get('max_spread')}** (threshold {disagreement.get('threshold')})",
            "",
        ]
    )
    for e in disagreement.get("entries") or []:
        lines.append(
            f"- {e.get('type')}: {e.get('high_confidence_agent')} ({e.get('high')}) vs "
            f"{e.get('low_confidence_agent')} ({e.get('low')})"
        )
    lines.append("")
    lines.append("## Synthesizer confidence")
    lines.append("")
    lines.append(f"{synthesizer_confidence if synthesizer_confidence is not None else 'unparsed'}/10")
    lines.append("")
    if errors:
        lines.append("## Errors / partial failures")
        lines.append("")
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    (session_path / "report.md").write_text("\n".join(lines), encoding="utf-8")

    audit_scores = {**scores, "synthesizer": synthesizer_confidence}
    audit = build_audit(
        synthesizer_text=outputs.get("synthesizer", ""),
        scores=audit_scores,
    )
    audit["meta"] = {
        "question": question,
        "epistemic_status": status,
        "disagreement": disagreement,
        "errors": errors,
        "models_used": models_used or {},
        "show_thinking": show_thinking,
    }
    (session_path / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")

    transcript_lines: list[str] = ["# Debate transcript", ""]
    for key in order:
        if key not in outputs:
            continue
        transcript_lines.append(f"## {key.upper()}")
        transcript_lines.append("")
        if models_used:
            mid = models_used.get(key) or models_used.get("default")
            if mid:
                transcript_lines.append(f"_Model: `{mid}`_")
                transcript_lines.append("")
        if show_thinking and thinking_outputs and thinking_outputs.get(key):
            transcript_lines.append("### Thinking")
            transcript_lines.append("")
            transcript_lines.append(thinking_outputs[key].strip())
            transcript_lines.append("")
            transcript_lines.append("### Public output")
            transcript_lines.append("")
        transcript_lines.append(outputs[key].strip())
        transcript_lines.append("")
    (session_path / "transcript.md").write_text("\n".join(transcript_lines), encoding="utf-8")
