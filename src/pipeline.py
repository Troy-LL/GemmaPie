from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from . import context, gemini_runner, scratchpad
from .parsing import extract_native_thoughts_from_raw, split_thinking_body


CONFIDENCE_RE = re.compile(r"Confidence:\s*(\d+)\s*/\s*10", re.IGNORECASE)
UNCERTAINTY_RE = re.compile(
    r"Key uncertainty:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL
)


def parse_confidence(text: str) -> int | None:
    m = CONFIDENCE_RE.search(text)
    if not m:
        return None
    try:
        v = int(m.group(1))
        if 0 <= v <= 10:
            return v
    except ValueError:
        return None
    return None


def parse_key_uncertainty(text: str) -> str | None:
    m = UNCERTAINTY_RE.search(text)
    if not m:
        return None
    s = m.group(1).strip()
    return s or None


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compose_full_prompt(system_prompt: str, task_context: str) -> str:
    return (
        "=== ROLE SYSTEM INSTRUCTIONS ===\n"
        f"{system_prompt.strip()}\n\n"
        "=== TASK AND CONTEXT ===\n"
        f"{task_context.strip()}\n"
    )


def build_disagreement_payload(
    scores: dict[str, int | None],
    outputs: dict[str, str],
    threshold: int,
) -> dict[str, Any]:
    valid = {k: v for k, v in scores.items() if v is not None}
    if len(valid) < 2:
        return {
            "threshold": threshold,
            "max_spread": 0,
            "high_disagreement": False,
            "agents": scores,
            "entries": [],
        }
    vals = list(valid.values())
    spread = max(vals) - min(vals)
    high = spread > threshold
    entries: list[dict[str, Any]] = []
    if high:
        hi_agent = max(valid, key=valid.get)
        lo_agent = min(valid, key=valid.get)
        entries.append(
            {
                "type": "confidence_divergence",
                "high_confidence_agent": hi_agent,
                "low_confidence_agent": lo_agent,
                "high": valid[hi_agent],
                "low": valid[lo_agent],
                "claim": parse_key_uncertainty(outputs.get(hi_agent, "")) or "(no key uncertainty parsed)",
                "counter_claim": parse_key_uncertainty(outputs.get(lo_agent, ""))
                or "(no key uncertainty parsed)",
            }
        )
    return {
        "threshold": threshold,
        "max_spread": spread,
        "high_disagreement": high,
        "agents": scores,
        "entries": entries,
    }


def run_pipeline(
    *,
    root: Path,
    session_path: Path,
    question: str,
    cfg: dict[str, Any],
    prompts_dir: Path,
    manual_pause: Callable[[str], None] | None = None,
    parallel_initial: bool = False,
    on_agent_start: Callable[[str, dict[str, Any]], None] | None = None,
    on_agent_done: Callable[[str, dict[str, Any]], None] | None = None,
    show_thinking: bool | None = None,
    prior_reference: str | None = None,
) -> dict[str, Any]:
    """
    Execute Researcher → Skeptic → Contrarian → Reviewer → [optional round2] → Synthesizer.

    Returns structured summary for reporting.

    If ``on_agent_start`` is set, it is called as ``(agent_id, meta)`` before each blocking
    ``gemini`` subprocess; ``meta`` has ``model``, ``timeout_s``, ``show_thinking``,
    ``prompt_chars``, or for the parallel first turn ``parallel: True`` and ``branches``.

    ``prior_reference``: optional markdown block injected only for the **researcher** role
    (similar prior session) to save downstream tokens while preserving a fresh multi-agent pass.
    """
    default_model = str(cfg.get("model", "gemini-2.0-flash"))
    models_map = cfg.get("models")
    if not isinstance(models_map, dict):
        models_map = {}

    def model_for(invoke_agent: str) -> str:
        """Per-agent `-m` id; round-2 steps inherit researcher/skeptic unless overridden."""
        sid = models_map.get(invoke_agent)
        if sid:
            return str(sid)
        if invoke_agent == "researcher_round2":
            sid = models_map.get("researcher")
        elif invoke_agent == "skeptic_round2":
            sid = models_map.get("skeptic")
        if sid:
            return str(sid)
        return default_model

    timeouts = cfg.get("timeouts") or {}
    default_timeout = float(timeouts.get("default", 180))
    disagreement_threshold = int(cfg.get("pipeline", {}).get("disagreement_threshold", 3))
    max_chars = int(cfg.get("context", {}).get("max_chars", 48000))
    thinking_cfg = cfg.get("thinking") or {}
    if show_thinking is None:
        show_thinking = bool(thinking_cfg.get("enabled", False))

    def timeout_for(agent: str) -> float:
        key = agent
        if agent == "skeptic_round2":
            key = "skeptic"
        elif agent == "researcher_round2":
            key = "researcher"
        return float(timeouts.get(key, default_timeout))

    prompts = {
        "researcher": load_text(prompts_dir / "researcher.txt"),
        "skeptic": load_text(prompts_dir / "skeptic.txt"),
        "contrarian": load_text(prompts_dir / "contrarian.txt"),
        "reviewer": load_text(prompts_dir / "reviewer.txt"),
        "synthesizer": load_text(prompts_dir / "synthesizer.txt"),
    }

    outputs: dict[str, str] = {}
    thinking_outputs: dict[str, str] = {}

    def commit_agent_output(agent: str, raw_text: str, res: dict[str, Any]) -> None:
        split = split_thinking_body(raw_text)
        native = extract_native_thoughts_from_raw(res.get("raw"))
        chunks: list[str] = []
        if native.strip():
            chunks.append("[from CLI JSON]\n" + native.strip())
        if split.thinking.strip():
            chunks.append(split.thinking.strip())
        combined = "\n\n".join(chunks).strip()
        outputs[agent] = split.public
        if combined:
            thinking_outputs[agent] = combined
        scratchpad.append_agent_section(session_path, agent, split.public)
        if agent in ("researcher", "researcher_round2"):
            scratchpad.merge_shared_facts_from_researcher(session_path, split.public)
        if show_thinking and combined:
            try:
                (session_path / f"{agent}_thinking.txt").write_text(combined + "\n", encoding="utf-8")
            except OSError:
                pass
        raw = res.get("raw")
        if raw is not None:
            try:
                (session_path / f"{agent}_raw.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
            except (TypeError, OSError):
                pass

    def invoke(agent: str, system_key: str, context_agent: str | None = None) -> dict[str, Any]:
        if manual_pause:
            manual_pause(agent)
        ctx_agent = context_agent or agent
        task = context.build_context_for(
            ctx_agent,
            question=question,
            outputs=outputs,
            session_path=session_path,
            max_chars=max_chars,
            parallel_initial=parallel_initial and agent in {"researcher", "skeptic"},
            prior_reference=prior_reference,
        )
        full_prompt = compose_full_prompt(prompts[system_key], task)
        if on_agent_start:
            on_agent_start(
                agent,
                {
                    "model": model_for(agent),
                    "timeout_s": timeout_for(agent),
                    "show_thinking": bool(show_thinking),
                    "prompt_chars": len(full_prompt),
                },
            )
        res = gemini_runner.run_gemini(
            full_prompt,
            model=model_for(agent),
            timeout=timeout_for(agent),
            cwd=session_path,
        )
        if res.get("ok") and isinstance(res.get("text"), str):
            commit_agent_output(agent, str(res["text"]), res)
        else:
            msg = res.get("error") or "unknown error"
            errors.append(f"{agent}: {msg}")
            outputs.setdefault(agent, f"({agent} failed: {msg})")
        res_out = dict(res)
        if show_thinking and thinking_outputs.get(agent):
            res_out["_thinking_preview"] = thinking_outputs[agent][:500]
        if on_agent_done:
            on_agent_done(agent, res_out)
        return res

    errors: list[str] = []

    if parallel_initial:
        if manual_pause:
            manual_pause("parallel: researcher+skeptic")

        task_pr = context.build_context_for(
            "researcher",
            question=question,
            outputs=outputs,
            session_path=session_path,
            max_chars=max_chars,
            parallel_initial=True,
            prior_reference=prior_reference,
        )
        fp_r = compose_full_prompt(prompts["researcher"], task_pr)
        task_ps = context.build_context_for(
            "skeptic",
            question=question,
            outputs=outputs,
            session_path=session_path,
            max_chars=max_chars,
            parallel_initial=True,
            prior_reference=None,
        )
        fp_s = compose_full_prompt(prompts["skeptic"], task_ps)
        if on_agent_start:
            on_agent_start(
                "__parallel__",
                {
                    "parallel": True,
                    "branches": [
                        {
                            "agent": "researcher",
                            "model": model_for("researcher"),
                            "timeout_s": timeout_for("researcher"),
                            "prompt_chars": len(fp_r),
                        },
                        {
                            "agent": "skeptic",
                            "model": model_for("skeptic"),
                            "timeout_s": timeout_for("skeptic"),
                            "prompt_chars": len(fp_s),
                        },
                    ],
                    "show_thinking": bool(show_thinking),
                },
            )

        def run_r() -> tuple[str, dict[str, Any]]:
            res = gemini_runner.run_gemini(
                fp_r,
                model=model_for("researcher"),
                timeout=timeout_for("researcher"),
                cwd=session_path,
            )
            return "researcher", res

        def run_s() -> tuple[str, dict[str, Any]]:
            res = gemini_runner.run_gemini(
                fp_s,
                model=model_for("skeptic"),
                timeout=timeout_for("skeptic"),
                cwd=session_path,
            )
            return "skeptic", res

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(run_r), ex.submit(run_s)]
            for fut in as_completed(futs):
                name, res = fut.result()
                if res.get("ok") and res.get("text"):
                    commit_agent_output(name, str(res["text"]), res)
                    res_out = dict(res)
                    if show_thinking and thinking_outputs.get(name):
                        res_out["_thinking_preview"] = thinking_outputs[name][:500]
                    if on_agent_done:
                        on_agent_done(name, res_out)
                else:
                    errors.append(f"{name}: {res.get('error') or 'unknown error'}")
                    outputs.setdefault(
                        name,
                        f"({name} failed: {res.get('error') or 'unknown error'})",
                    )
                    res_out = dict(res)
                    if on_agent_done:
                        on_agent_done(name, res_out)
        if "researcher" not in outputs:
            errors.append("researcher failed in parallel mode; aborting early.")
        if "skeptic" not in outputs:
            errors.append("skeptic failed in parallel mode; continuing with empty skeptic context.")
            outputs.setdefault("skeptic", "(Skeptic pass failed or timed out.)")
    else:
        invoke("researcher", "researcher")
        invoke("skeptic", "skeptic")

    invoke("contrarian", "contrarian")
    invoke("reviewer", "reviewer")

    scores_main: dict[str, int | None] = {
        "researcher": parse_confidence(outputs.get("researcher", "")),
        "skeptic": parse_confidence(outputs.get("skeptic", "")),
        "contrarian": parse_confidence(outputs.get("contrarian", "")),
        "reviewer": parse_confidence(outputs.get("reviewer", "")),
    }
    disagreement = build_disagreement_payload(scores_main, outputs, disagreement_threshold)
    disagreements_path = session_path / "disagreements.json"
    disagreements_path.write_text(json.dumps(disagreement, indent=2), encoding="utf-8")

    if disagreement["high_disagreement"]:
        summary_bits = [
            f"Max confidence spread: {disagreement['max_spread']} (threshold {disagreement_threshold}).",
        ]
        for e in disagreement["entries"]:
            summary_bits.append(
                f"Divergence: {e.get('high_confidence_agent')} ({e.get('high')}) vs "
                f"{e.get('low_confidence_agent')} ({e.get('low')})."
            )
        outputs["disagreement_summary"] = "\n".join(summary_bits)

        invoke("skeptic_round2", "skeptic", context_agent="skeptic_round2")
        invoke("researcher_round2", "researcher", context_agent="researcher_round2")

    invoke("synthesizer", "synthesizer")

    return {
        "question": question,
        "outputs": outputs,
        "disagreement": disagreement,
        "errors": errors,
        "scores": scores_main,
        "synthesizer_confidence": parse_confidence(outputs.get("synthesizer", "")),
        "models_used": {
            "default": default_model,
            "researcher": model_for("researcher"),
            "skeptic": model_for("skeptic"),
            "contrarian": model_for("contrarian"),
            "reviewer": model_for("reviewer"),
            "synthesizer": model_for("synthesizer"),
            "researcher_round2": model_for("researcher_round2"),
            "skeptic_round2": model_for("skeptic_round2"),
        },
        "thinking_outputs": thinking_outputs,
        "show_thinking": show_thinking,
    }
