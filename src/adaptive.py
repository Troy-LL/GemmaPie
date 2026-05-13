"""Adaptive tier routing: trivial arithmetic (T0), light multi-agent (T1), full pipeline (T2)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src import gemini_runner

from . import scratchpad

Tier = Literal["T0", "T1", "T2", "unknown"]


@dataclass
class ClassifyResult:
    tier: str
    reason: str
    t0_a: int | None = None
    t0_b: int | None = None
    t0_sum: int | None = None


def parse_trivial_add(question: str, max_digits: int) -> tuple[int, int, int] | None:
    """
    Match ``^\\s*(\\d{1,D})\\s*+\\s*(\\d{1,D})\\s*$`` with D=max_digits; return (a, b, a+b) or None.
    """
    d = max(1, min(int(max_digits), 20))
    pat = re.compile(rf"^\s*(\d{{1,{d}}})\s*\+\s*(\d{{1,{d}}})\s*$")
    for candidate in (question, question.strip()):
        m = pat.match(candidate)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            return a, b, a + b
    return None


def classify_heuristic(question: str, max_digits: int) -> ClassifyResult:
    t = parse_trivial_add(question, max_digits)
    if t is None:
        return ClassifyResult(tier="unknown", reason="not_trivial_add")
    a, b, total = t
    return ClassifyResult(
        tier="T0",
        reason=f"trivial_addition:{a}+{b}={total}",
        t0_a=a,
        t0_b=b,
        t0_sum=total,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
    if fence:
        blob = fence.group(1).strip()
        try:
            data = json.loads(blob)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            data = json.loads(s[i : j + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def classify_slm(question: str, cfg: dict[str, Any], session_path: Path) -> ClassifyResult:
    """One ``run_gemini`` with router model; JSON ``{tier,reason}`` only; any failure → T2 ``router_parse_failed``."""
    adapt = cfg.get("adaptive") or {}
    if not isinstance(adapt, dict):
        adapt = {}
    raw_model = adapt.get("router_model")
    if raw_model is None or (isinstance(raw_model, str) and not raw_model.strip()):
        raw_model = cfg.get("model") or "gemini-2.5-flash"
    router_model = str(raw_model).strip()
    timeout = float(adapt.get("router_timeout", 30))
    prompt = (
        "Reply with ONLY a single JSON object. No markdown fences, no extra prose.\n"
        'Schema: {"tier":"T1"|"T2","reason":"short string"}\n'
        "- T1: the question is simple or narrow enough that one research-style pass plus synthesis is likely enough.\n"
        "- T2: the question needs full multi-agent critique, debate, or careful verification.\n\n"
        f"User question:\n{question.strip()}\n"
    )
    res = gemini_runner.run_gemini(
        prompt,
        model=router_model,
        timeout=timeout,
        cwd=session_path,
    )
    if not res.get("ok") or not isinstance(res.get("text"), str):
        return ClassifyResult(tier="T2", reason="router_parse_failed")
    parsed = _extract_json_object(str(res["text"]))
    if not parsed:
        return ClassifyResult(tier="T2", reason="router_parse_failed")
    tier_raw = parsed.get("tier")
    reason_raw = parsed.get("reason", "")
    tier = str(tier_raw).strip().upper() if tier_raw is not None else ""
    reason = str(reason_raw).strip() or "slm_router"
    if tier == "T1":
        return ClassifyResult(tier="T1", reason=reason)
    if tier == "T2":
        return ClassifyResult(tier="T2", reason=reason)
    return ClassifyResult(tier="T2", reason="router_parse_failed")


def apply_t0_to_session(
    session_path: Path,
    question: str,
    a: int,
    b: int,
    total: int,
) -> dict[str, str]:
    """
    Deterministic T0 outputs (no Gemini): researcher note + synthesizer narrative with ``## Claims``.
    Appends both roles to the session scratchpad.
    """
    researcher = (
        f"Trivial arithmetic (adaptive T0): {a} + {b} = {total}; no substantive research pass required."
    )
    claims_json = json.dumps(
        [
            {
                "claim": f"{a} + {b} = {total}",
                "text": f"{a} + {b} = {total}",
                "supporting_agents": ["researcher"],
                "status": "verified",
            }
        ],
        indent=2,
    )
    synthesizer = (
        f"The user asked `{question.strip()}`. Integer addition gives **{a} + {b} = {total}**.\n\n"
        "## Claims\n"
        "```json\n"
        f"{claims_json}\n"
        "```\n\n"
        "Confidence: 10/10\n"
        "Key uncertainty: none (arithmetic)\n"
    )
    scratchpad.append_agent_section(session_path, "researcher", researcher)
    scratchpad.append_agent_section(session_path, "synthesizer", synthesizer)
    return {"researcher": researcher, "synthesizer": synthesizer}
