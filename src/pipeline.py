from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from . import adaptive, context, gemini_runner, scratchpad
from .parsing import extract_native_thoughts_from_raw, split_thinking_body


# Passed to the dashboard / optional stderr echo after each step (not streamed mid-call).
_THINKING_PREVIEW_CHARS = 8000

CONFIDENCE_RE = re.compile(r"Confidence:\s*(\d+)\s*/\s*10", re.IGNORECASE)
UNCERTAINTY_RE = re.compile(
    r"Key uncertainty:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL
)

_SKIP_T0 = "(Not invoked: adaptive tier T0.)"
_SKIP_T1 = "(Not invoked: adaptive tier T1.)"

# Windows CreateProcess command-line limit (~8191). Failure messages can embed
# long CLI stderr; keep placeholders short so later agents (especially the
# synthesizer) still fit when passed as `gemini -p "<huge string>"`.
_FAILURE_SNIP_LEN = 700


def _failure_placeholder(agent: str, msg: str) -> str:
    m = (msg or "unknown error").strip()
    if len(m) > _FAILURE_SNIP_LEN:
        m = m[: _FAILURE_SNIP_LEN - 3] + "..."
    return f"({agent} failed: {m})"


def _max_concurrent_from_cfg(cfg: dict[str, Any]) -> int:
    rl = cfg.get("rate_limit")
    if not isinstance(rl, dict):
        return 2
    return max(1, int(rl.get("max_concurrent", 2) or 2))


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


def _write_adaptive_tier_json(
    session_path: Path, tier: str, meta: dict[str, Any] | None
) -> None:
    payload: dict[str, Any] = {"tier": tier}
    if meta:
        payload.update(meta)
    try:
        (session_path / "adaptive_tier.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _run_full_pipeline_core(
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
    knowledge_reference: str | None = None,
    knowledge_roles: set[str] | None = None,
    tier_locked_t2: bool = False,
    adaptive_light: bool = False,
) -> dict[str, Any]:
    """
    Full sequential/parallel/round-2 pipeline. ``tier_locked_t2`` is True when T1 fallback reruns
    the full stack so adaptive routing cannot re-enter. ``adaptive_light`` uses ``adaptive.models_light``
    for researcher/synthesizer when not ``tier_locked_t2``.
    """
    default_model = str(cfg.get("model", "gemini-2.5-flash"))
    models_map = cfg.get("models")
    if not isinstance(models_map, dict):
        models_map = {}

    def model_for(invoke_agent: str) -> str:
        """Per-agent `-m` id; round-2 steps inherit researcher/skeptic unless overridden."""
        if adaptive_light and not tier_locked_t2 and invoke_agent in ("researcher", "synthesizer"):
            ac = cfg.get("adaptive") or {}
            if isinstance(ac, dict):
                light = ac.get("models_light")
                if isinstance(light, dict):
                    lid = light.get(invoke_agent)
                    if lid and str(lid).strip():
                        return str(lid).strip()
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

    errors: list[str] = []
    max_concurrent = _max_concurrent_from_cfg(cfg)

    def kb_for(agent_name: str) -> str | None:
        if not knowledge_reference:
            return None
        if not knowledge_roles:
            return knowledge_reference
        return knowledge_reference if agent_name.lower() in knowledge_roles else None

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
            knowledge_reference=kb_for(ctx_agent),
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
            outputs.setdefault(agent, _failure_placeholder(agent, msg))
        res_out = dict(res)
        if show_thinking and thinking_outputs.get(agent):
            res_out["_thinking_preview"] = thinking_outputs[agent][:_THINKING_PREVIEW_CHARS]
        if on_agent_done:
            on_agent_done(agent, res_out)
        return res

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
            knowledge_reference=kb_for("researcher"),
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
            knowledge_reference=kb_for("skeptic"),
        )
        fp_s = compose_full_prompt(prompts["skeptic"], task_ps)

        def finish_parallel_agent(name: str, res: dict[str, Any]) -> None:
            if res.get("ok") and res.get("text"):
                commit_agent_output(name, str(res["text"]), res)
                res_out = dict(res)
                if show_thinking and thinking_outputs.get(name):
                    res_out["_thinking_preview"] = thinking_outputs[name][:_THINKING_PREVIEW_CHARS]
                if on_agent_done:
                    on_agent_done(name, res_out)
            else:
                err = res.get("error") or "unknown error"
                errors.append(f"{name}: {err}")
                outputs.setdefault(name, _failure_placeholder(name, err))
                res_out = dict(res)
                if on_agent_done:
                    on_agent_done(name, res_out)

        if max_concurrent >= 2:
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
                    finish_parallel_agent(name, res)
        else:
            # Same parallel-mode prompts (Skeptic does not see Researcher), but one
            # subprocess at a time when rate_limit.max_concurrent is 1.
            if on_agent_start:
                on_agent_start(
                    "researcher",
                    {
                        "model": model_for("researcher"),
                        "timeout_s": timeout_for("researcher"),
                        "show_thinking": bool(show_thinking),
                        "prompt_chars": len(fp_r),
                        "parallel_initial_sequential": True,
                    },
                )
            res_r = gemini_runner.run_gemini(
                fp_r,
                model=model_for("researcher"),
                timeout=timeout_for("researcher"),
                cwd=session_path,
            )
            finish_parallel_agent("researcher", res_r)
            if on_agent_start:
                on_agent_start(
                    "skeptic",
                    {
                        "model": model_for("skeptic"),
                        "timeout_s": timeout_for("skeptic"),
                        "show_thinking": bool(show_thinking),
                        "prompt_chars": len(fp_s),
                        "parallel_initial_sequential": True,
                    },
                )
            res_s = gemini_runner.run_gemini(
                fp_s,
                model=model_for("skeptic"),
                timeout=timeout_for("skeptic"),
                cwd=session_path,
            )
            finish_parallel_agent("skeptic", res_s)
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
    knowledge_reference: str | None = None,
    knowledge_roles: set[str] | None = None,
    adaptive_tier: str | None = None,
    adaptive_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute Researcher → Skeptic → Contrarian → Reviewer → [optional round2] → Synthesizer,
    or adaptive T0/T1/T2 branches when ``adaptive_tier`` is set.

    Returns structured summary for reporting.

    If ``on_agent_start`` is set, it is called as ``(agent_id, meta)`` before each blocking
    ``gemini`` subprocess; ``meta`` has ``model``, ``timeout_s``, ``show_thinking``,
    ``prompt_chars``, or for the parallel first turn ``parallel: True`` and ``branches``.

    ``prior_reference``: optional markdown block injected only for the **researcher** role
    (similar prior session) to save downstream tokens while preserving a fresh multi-agent pass.
    """
    if adaptive_tier is None:
        summary = _run_full_pipeline_core(
            root=root,
            session_path=session_path,
            question=question,
            cfg=cfg,
            prompts_dir=prompts_dir,
            manual_pause=manual_pause,
            parallel_initial=parallel_initial,
            on_agent_start=on_agent_start,
            on_agent_done=on_agent_done,
            show_thinking=show_thinking,
            prior_reference=prior_reference,
            knowledge_reference=knowledge_reference,
            knowledge_roles=knowledge_roles,
            tier_locked_t2=False,
            adaptive_light=False,
        )
        return summary

    default_model = str(cfg.get("model", "gemini-2.5-flash"))
    models_map = cfg.get("models")
    if not isinstance(models_map, dict):
        models_map = {}
    adapt_cfg = cfg.get("adaptive") or {}
    if not isinstance(adapt_cfg, dict):
        adapt_cfg = {}
    disagreement_threshold = int(cfg.get("pipeline", {}).get("disagreement_threshold", 3))
    thinking_cfg = cfg.get("thinking") or {}
    eff_show = show_thinking
    if eff_show is None:
        eff_show = bool(thinking_cfg.get("enabled", False))

    meta_out: dict[str, Any] = dict(adaptive_meta or {})

    if adaptive_tier == "T0":
        max_digits = int(adapt_cfg.get("max_trivial_add_digits", 6))
        parsed = adaptive.parse_trivial_add(question, max_digits)
        if parsed is None:
            summary = _run_full_pipeline_core(
                root=root,
                session_path=session_path,
                question=question,
                cfg=cfg,
                prompts_dir=prompts_dir,
                manual_pause=manual_pause,
                parallel_initial=parallel_initial,
                on_agent_start=on_agent_start,
                on_agent_done=on_agent_done,
                show_thinking=show_thinking,
                prior_reference=prior_reference,
                knowledge_reference=knowledge_reference,
                knowledge_roles=knowledge_roles,
                tier_locked_t2=False,
                adaptive_light=False,
            )
            mu = summary.get("models_used")
            if isinstance(mu, dict):
                summary["models_used"] = {**mu, "_tier": "T2"}
            _write_adaptive_tier_json(
                session_path,
                "T2",
                {**meta_out, "reason": "t0_reparse_failed", "t0_fallback": True},
            )
            return summary
        a, b, total = parsed
        core_out = adaptive.apply_t0_to_session(session_path, question, a, b, total)
        outputs: dict[str, str] = {
            "researcher": core_out["researcher"],
            "skeptic": _SKIP_T0,
            "contrarian": _SKIP_T0,
            "reviewer": _SKIP_T0,
            "skeptic_round2": _SKIP_T0,
            "researcher_round2": _SKIP_T0,
            "synthesizer": core_out["synthesizer"],
        }
        scores_main: dict[str, int | None] = {
            "researcher": parse_confidence(outputs["researcher"]),
            "skeptic": None,
            "contrarian": None,
            "reviewer": None,
        }
        disagreement = build_disagreement_payload(scores_main, outputs, disagreement_threshold)
        (session_path / "disagreements.json").write_text(
            json.dumps(disagreement, indent=2),
            encoding="utf-8",
        )
        models_used = {
            "default": default_model,
            "researcher": "(T0)",
            "skeptic": "(T0)",
            "contrarian": "(T0)",
            "reviewer": "(T0)",
            "synthesizer": "(T0)",
            "researcher_round2": "(T0)",
            "skeptic_round2": "(T0)",
            "_tier": "T0",
        }
        _write_adaptive_tier_json(session_path, "T0", meta_out)
        return {
            "question": question,
            "outputs": outputs,
            "disagreement": disagreement,
            "errors": [],
            "scores": scores_main,
            "synthesizer_confidence": parse_confidence(outputs.get("synthesizer", "")),
            "models_used": models_used,
            "thinking_outputs": {},
            "show_thinking": eff_show,
        }

    if adaptive_tier == "T2":
        summary = _run_full_pipeline_core(
            root=root,
            session_path=session_path,
            question=question,
            cfg=cfg,
            prompts_dir=prompts_dir,
            manual_pause=manual_pause,
            parallel_initial=parallel_initial,
            on_agent_start=on_agent_start,
            on_agent_done=on_agent_done,
            show_thinking=show_thinking,
            prior_reference=prior_reference,
            knowledge_reference=knowledge_reference,
            knowledge_roles=knowledge_roles,
            tier_locked_t2=False,
            adaptive_light=False,
        )
        mu = summary.get("models_used")
        if isinstance(mu, dict):
            summary["models_used"] = {**mu, "_tier": "T2"}
        _write_adaptive_tier_json(session_path, "T2", meta_out)
        return summary

    # --- T1 ---
    max_chars = int(cfg.get("context", {}).get("max_chars", 48000))
    timeouts = cfg.get("timeouts") or {}
    default_timeout = float(timeouts.get("default", 180))

    def model_for_t1(invoke_agent: str) -> str:
        light = adapt_cfg.get("models_light")
        if isinstance(light, dict) and invoke_agent in ("researcher", "synthesizer"):
            lid = light.get(invoke_agent)
            if lid and str(lid).strip():
                return str(lid).strip()
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

    outputs_t1: dict[str, str] = {}
    thinking_t1: dict[str, str] = {}
    errors_t1: list[str] = []

    def commit_agent_output_t1(agent: str, raw_text: str, res: dict[str, Any]) -> None:
        split = split_thinking_body(raw_text)
        native = extract_native_thoughts_from_raw(res.get("raw"))
        chunks: list[str] = []
        if native.strip():
            chunks.append("[from CLI JSON]\n" + native.strip())
        if split.thinking.strip():
            chunks.append(split.thinking.strip())
        combined = "\n\n".join(chunks).strip()
        outputs_t1[agent] = split.public
        if combined:
            thinking_t1[agent] = combined
        scratchpad.append_agent_section(session_path, agent, split.public)
        if agent == "researcher":
            scratchpad.merge_shared_facts_from_researcher(session_path, split.public)
        if eff_show and combined:
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

    def invoke_t1(agent: str, system_key: str, context_agent: str | None = None) -> dict[str, Any]:
        if manual_pause:
            manual_pause(agent)
        ctx_agent = context_agent or agent
        task = context.build_context_for(
            ctx_agent,
            question=question,
            outputs=outputs_t1,
            session_path=session_path,
            max_chars=max_chars,
            parallel_initial=False,
            prior_reference=prior_reference,
            knowledge_reference=(
                knowledge_reference
                if (
                    not knowledge_roles
                    or ctx_agent.lower() in knowledge_roles
                )
                else None
            ),
        )
        full_prompt = compose_full_prompt(prompts[system_key], task)
        if on_agent_start:
            on_agent_start(
                agent,
                {
                    "model": model_for_t1(agent),
                    "timeout_s": timeout_for(agent),
                    "show_thinking": bool(eff_show),
                    "prompt_chars": len(full_prompt),
                },
            )
        res = gemini_runner.run_gemini(
            full_prompt,
            model=model_for_t1(agent),
            timeout=timeout_for(agent),
            cwd=session_path,
        )
        if res.get("ok") and isinstance(res.get("text"), str):
            commit_agent_output_t1(agent, str(res["text"]), res)
        else:
            msg = res.get("error") or "unknown error"
            errors_t1.append(f"{agent}: {msg}")
            outputs_t1.setdefault(agent, _failure_placeholder(agent, msg))
        res_out = dict(res)
        if eff_show and thinking_t1.get(agent):
            res_out["_thinking_preview"] = thinking_t1[agent][:_THINKING_PREVIEW_CHARS]
        if on_agent_done:
            on_agent_done(agent, res_out)
        return res

    invoke_t1("researcher", "researcher")
    outputs_t1["skeptic"] = _SKIP_T1
    outputs_t1["contrarian"] = _SKIP_T1
    outputs_t1["reviewer"] = _SKIP_T1

    scores_t1: dict[str, int | None] = {
        "researcher": parse_confidence(outputs_t1.get("researcher", "")),
        "skeptic": None,
        "contrarian": None,
        "reviewer": None,
    }
    disagreement_t1 = build_disagreement_payload(scores_t1, outputs_t1, disagreement_threshold)
    (session_path / "disagreements.json").write_text(
        json.dumps(disagreement_t1, indent=2),
        encoding="utf-8",
    )

    synth_res = invoke_t1("synthesizer", "synthesizer")
    synth_text = str(outputs_t1.get("synthesizer", "") or "").strip()

    req_claims = bool(adapt_cfg.get("require_claims_for_t1_fallback", True))
    fallback_ok = bool(adapt_cfg.get("fallback_to_full", True))
    has_claims = "## claims" in synth_text.lower()
    fb_reason: str | None = None
    if any(e.startswith("synthesizer:") for e in errors_t1) or not synth_res.get("ok"):
        fb_reason = "synthesizer_error"
    elif req_claims and not has_claims:
        fb_reason = "claims_missing"
    synth_broken = fb_reason is not None

    if fallback_ok and synth_broken:
        summary_fb = _run_full_pipeline_core(
            root=root,
            session_path=session_path,
            question=question,
            cfg=cfg,
            prompts_dir=prompts_dir,
            manual_pause=manual_pause,
            parallel_initial=parallel_initial,
            on_agent_start=on_agent_start,
            on_agent_done=on_agent_done,
            show_thinking=show_thinking,
            prior_reference=prior_reference,
            knowledge_reference=knowledge_reference,
            knowledge_roles=knowledge_roles,
            tier_locked_t2=True,
            adaptive_light=False,
        )
        meta_fb = {**meta_out, "t1_fallback": True, "t1_fallback_reason": fb_reason or "unknown"}
        mu = summary_fb.get("models_used")
        if isinstance(mu, dict):
            summary_fb["models_used"] = {**mu, "_tier": "T2", "_t1_fallback": True}
        _write_adaptive_tier_json(session_path, "T2", meta_fb)
        return summary_fb

    _nm = "(adaptive T1 — not invoked)"
    models_used_t1 = {
        "default": default_model,
        "researcher": model_for_t1("researcher"),
        "skeptic": _nm,
        "contrarian": _nm,
        "reviewer": _nm,
        "synthesizer": model_for_t1("synthesizer"),
        "researcher_round2": _nm,
        "skeptic_round2": _nm,
        "_tier": "T1",
    }
    outputs_t1["skeptic_round2"] = _SKIP_T1
    outputs_t1["researcher_round2"] = _SKIP_T1

    _write_adaptive_tier_json(session_path, "T1", meta_out)
    return {
        "question": question,
        "outputs": outputs_t1,
        "disagreement": disagreement_t1,
        "errors": errors_t1,
        "scores": scores_t1,
        "synthesizer_confidence": parse_confidence(outputs_t1.get("synthesizer", "")),
        "models_used": models_used_t1,
        "thinking_outputs": thinking_t1,
        "show_thinking": eff_show,
    }
