#!/usr/bin/env python3
"""
CLI entry: multi-agent critique pipeline via Gemini CLI (headless JSON).

Usage:
  python orchestrate.py "Is nuclear energy safe?"
  python orchestrate.py --manual "Question here"
  python orchestrate.py --parallel "Question here"

After each run, the Synthesizer's integrated answer is printed under FINAL ANSWER and saved as
sessions/.../final_answer.txt (alongside report.md). Optional session reuse: see
docs/SESSION_REUSE_USER_GUIDE.md. Zero-call shortcut needs allow_zero_call_reuse or --allow-zero-call-reuse.
Optional adaptive tier routing: docs/ADAPTIVE_TIERS.md and config adaptive / --adaptive.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src import adaptive, gemini_runner, reporting, scratchpad, session_cache
from src.dashboard import Dashboard
from src.parsing import strip_synthesizer_claims_block
from src.pipeline import parse_confidence, run_pipeline


def _final_answer_body(outputs: dict[str, Any]) -> str:
    """Integrated answer for the user: Synthesizer when OK, else Reviewer, else a short notice."""
    synth = str(outputs.get("synthesizer", "") or "").strip()
    if synth and not synth.startswith("(synthesizer failed"):
        return strip_synthesizer_claims_block(synth)
    rev = str(outputs.get("reviewer", "") or "").strip()
    if rev and not rev.startswith("(reviewer failed"):
        return (
            "[Synthesizer did not complete; below is the reviewer integration draft.]\n\n" + rev
        )
    return (
        "[No final integrated answer was produced. Open report.md in the session folder "
        "for agent traces and errors.]"
    )


def _print_and_save_final_answer(session_path: Path, question: str, summary: dict[str, Any]) -> None:
    outputs = summary.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {}
    body = _final_answer_body(outputs)
    banner = "=" * 72
    block = (
        f"\n{banner}\n"
        "FINAL ANSWER (integrated for the user)\n"
        f"{banner}\n\n"
        f"Question: {question.strip()}\n\n"
        f"{body}\n\n"
        f"{banner}\n"
    )
    print(block, file=sys.stdout)
    try:
        (session_path / "final_answer.txt").write_text(
            block.lstrip("\n") + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _write_synthesizer_public(session_path: Path, outputs: dict[str, Any]) -> None:
    """Plain synthesizer text for future session_reuse matching (no markdown banners)."""
    synth = str(outputs.get("synthesizer", "") or "").strip()
    if synth and not synth.startswith("(synthesizer failed"):
        try:
            (session_path / "synthesizer_public.txt").write_text(synth + "\n", encoding="utf-8")
        except OSError:
            pass


def _short_circuit_summary(
    question: str,
    prior_session_name: str,
    similarity: float,
    synth_body: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    thr = int((cfg.get("pipeline") or {}).get("disagreement_threshold", 3))
    default_model = str(cfg.get("model", "gemini-2.5-flash"))
    models_map = cfg.get("models")
    if not isinstance(models_map, dict):
        models_map = {}

    def mf(key: str) -> str:
        return str(models_map.get(key) or default_model)

    disagreement = {
        "threshold": thr,
        "max_spread": 0,
        "high_disagreement": False,
        "agents": {},
        "entries": [],
    }
    return {
        "question": question,
        "outputs": {"synthesizer": synth_body.strip()},
        "disagreement": disagreement,
        "errors": [],
        "scores": {},
        "synthesizer_confidence": parse_confidence(synth_body),
        "models_used": {
            "default": default_model,
            "researcher": mf("researcher"),
            "skeptic": mf("skeptic"),
            "contrarian": mf("contrarian"),
            "reviewer": mf("reviewer"),
            "synthesizer": mf("synthesizer"),
            "researcher_round2": mf("researcher_round2"),
            "skeptic_round2": mf("skeptic_round2"),
        },
        "thinking_outputs": {},
        "show_thinking": False,
        "adaptive_tier": None,
        "adaptive_meta": None,
    }


def _load_cfg(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("config.yaml must contain a mapping at the top level.")
    return data


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Distributed cognition orchestrator (Gemini CLI).")
    parser.add_argument(
        "question",
        nargs=argparse.REMAINDER,
        help="User question (quote the full string).",
    )
    parser.add_argument("--config", type=Path, default=root / "config.yaml")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Pause before each agent step (press Enter in the terminal).",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help=(
            "Run Researcher and Skeptic in parallel on the initial question only. "
            "Skeptic does not see the Researcher draft until later agents; Contrarian onward are sequential."
        ),
    )
    parser.add_argument("--no-dashboard", action="store_true", help="Disable Rich live dashboard.")
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Include thinking traces in reports/transcript and write *_thinking.txt (overrides config unless --no-thinking).",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable thinking trace output for this run.",
    )
    parser.add_argument(
        "--echo-thinking",
        action="store_true",
        help=(
            "After each agent step, print parsed thinking to stderr (same text as the dashboard panel). "
            "Still no live streaming while gemini runs."
        ),
    )
    parser.add_argument(
        "--reuse-similar",
        action="store_true",
        help="Enable prior-session reuse (see session_reuse in config.yaml, or SESSION_REUSE=1).",
    )
    parser.add_argument(
        "--reuse-mode",
        choices=("inject", "short_circuit", "off"),
        default=None,
        help="Override config session_reuse.mode when reuse is enabled.",
    )
    parser.add_argument(
        "--reuse-similarity",
        type=float,
        default=None,
        help="Override session_reuse.similarity_threshold (0.0-1.0, e.g. 0.92).",
    )
    parser.add_argument(
        "--allow-zero-call-reuse",
        action="store_true",
        help=(
            "Expert only: allow mode short_circuit to copy a prior answer with zero Gemini calls. "
            "See docs/SESSION_REUSE_USER_GUIDE.md; stale answers are possible."
        ),
    )
    parser.add_argument(
        "--adaptive",
        choices=("off", "heuristic", "heuristic_then_slm"),
        default=None,
        help=(
            "Adaptive tier routing override: `off` disables routing for this run; otherwise overrides "
            "config `adaptive.router`. See docs/ADAPTIVE_TIERS.md."
        ),
    )
    args = parser.parse_args(argv)

    q_parts = [p for p in (args.question or []) if p.strip()]
    if not q_parts:
        parser.error('Provide a question string, e.g. python orchestrate.py "Is nuclear energy safe?"')
    question = " ".join(q_parts).strip()

    cfg_path = args.config
    if not cfg_path.is_file():
        print(f"Missing config: {cfg_path}", file=sys.stderr)
        return 2
    cfg = _load_cfg(cfg_path)
    gemini_runner.apply_rate_limit_settings(cfg.get("rate_limit"))

    env_on = os.environ.get("SHOW_AGENT_THINKING", "").strip().lower() in ("1", "true", "yes")
    thinking_yaml = cfg.get("thinking") or {}
    if not isinstance(thinking_yaml, dict):
        thinking_yaml = {}
    cfg_thinking = bool(thinking_yaml.get("enabled", False))
    if args.no_thinking:
        eff_thinking = False
    elif args.show_thinking:
        eff_thinking = True
    else:
        eff_thinking = cfg_thinking or env_on

    echo_thinking = (
        bool(thinking_yaml.get("echo_terminal", False))
        or os.environ.get("ECHO_AGENT_THINKING", "").strip().lower() in ("1", "true", "yes")
        or bool(args.echo_thinking)
    )

    prompts_dir = root / str(cfg.get("paths", {}).get("prompts_dir", "prompts"))
    if not prompts_dir.is_dir():
        print(f"Missing prompts dir: {prompts_dir}", file=sys.stderr)
        return 2

    session_id = scratchpad.new_session_id()
    session_path = scratchpad.ensure_session_layout(root, session_id)
    (session_path / "question.txt").write_text(question + "\n", encoding="utf-8")

    parallel = bool(args.parallel or cfg.get("pipeline", {}).get("parallel_initial"))

    reuse_cfg = cfg.get("session_reuse") or {}
    if not isinstance(reuse_cfg, dict):
        reuse_cfg = {}
    reuse_env = os.environ.get("SESSION_REUSE", "").strip().lower() in ("1", "true", "yes")
    reuse_enabled = bool(args.reuse_similar) or bool(reuse_cfg.get("enabled")) or reuse_env
    cfg_mode = str(reuse_cfg.get("mode") or "inject").strip().lower()
    if cfg_mode not in ("inject", "short_circuit", "off"):
        cfg_mode = "inject"
    reuse_mode_effective = args.reuse_mode if args.reuse_mode is not None else cfg_mode
    if reuse_mode_effective not in ("inject", "short_circuit", "off"):
        reuse_mode_effective = "inject"

    allow_zero_call = bool(reuse_cfg.get("allow_zero_call_reuse", False)) or bool(
        args.allow_zero_call_reuse
    )
    run_mode = reuse_mode_effective
    if run_mode == "short_circuit" and not allow_zero_call:
        print(
            "\n[GemmaPie] Session reuse: `short_circuit` (copy old answer, zero API calls) is turned OFF "
            "for safety — old answers can be wrong or mismatched.\n"
            "Using **inject** instead if a prior session matches (full debate still runs).\n"
            "Experts: set `allow_zero_call_reuse: true` in config.yaml or pass `--allow-zero-call-reuse`.\n"
            "Everyone else: see docs/SESSION_REUSE_USER_GUIDE.md\n",
            file=sys.stdout,
        )
        run_mode = "inject"

    sessions_root = root / "sessions"
    short_circuited = False
    prior_reference: str | None = None
    log_reuse = "none"
    matched_name: str | None = None
    matched_sim_log: float | None = None
    matched_word_log: float | None = None
    matched_age_days: float | None = None
    prior_q = ""
    summary: dict[str, Any] = {}
    adaptive_tier: str | None = None
    adaptive_meta: dict[str, Any] | None = None

    if reuse_enabled and reuse_mode_effective != "off":
        thr = (
            float(args.reuse_similarity)
            if args.reuse_similarity is not None
            else float(reuse_cfg.get("similarity_threshold", 0.88))
        )
        thr = max(0.0, min(1.0, thr))
        min_wo = float(reuse_cfg.get("min_word_overlap", 0.14))
        min_wo = max(0.0, min(1.0, min_wo))
        raw_age = reuse_cfg.get("max_reuse_session_age_days", 14)
        max_age: float | None
        if raw_age is None:
            max_age = None
        else:
            max_age = float(raw_age)
        max_prior = int(reuse_cfg.get("max_prior_chars", 8000))
        match = session_cache.find_best_prior_session(
            sessions_root=sessions_root,
            question=question,
            exclude_session=session_path,
            similarity_threshold=thr,
            min_word_overlap=min_wo,
            max_session_age_days=max_age,
        )
        if match.session_dir is not None:
            match_path = match.session_dir
            prior_q = match.prior_question
            match_sim = match.char_similarity
            cached = session_cache.read_prior_integrated_answer(match_path)
            if cached:
                matched_name = match_path.name
                matched_sim_log = match_sim
                matched_word_log = match.word_overlap
                matched_age_days = match.age_days
                if run_mode == "short_circuit":
                    summary = _short_circuit_summary(question, match_path.name, match_sim, cached, cfg)
                    short_circuited = True
                    log_reuse = "short_circuit"
                elif run_mode == "inject":
                    prior_reference = session_cache.format_prior_reference_block(
                        prior_session=match_path,
                        prior_question=prior_q,
                        prior_answer=cached,
                        similarity=match_sim,
                        word_overlap=match.word_overlap,
                        max_chars=max_prior,
                    )
                    log_reuse = "inject"

    def manual_pause(agent: str) -> None:
        if not args.manual:
            return
        input(f"\n[manual] Press Enter to run `{agent}`...\n")

    dash_ctx = None
    dashboard: Dashboard | None = None
    if not args.no_dashboard:
        dashboard = Dashboard()
        dash_ctx = dashboard.__enter__()

    step_started_at: dict[str, float] = {}
    step_watchdog_stop: dict[str, threading.Event] = {}

    try:
        parallel_pending_branches = 0

        def _ts() -> str:
            return datetime.now().strftime("%H:%M:%S")

        def _start_watchdog(agent: str, interval_s: float = 12.0) -> None:
            # Plain-log heartbeat when dashboard is off. With Rich Live on, the dashboard
            # tick thread updates elapsed time; printing here fights the Live panel.
            if dashboard is not None and not args.no_dashboard:
                return
            stop = threading.Event()
            step_watchdog_stop[agent] = stop

            def _runner() -> None:
                while not stop.wait(interval_s):
                    started = step_started_at.get(agent)
                    if isinstance(started, (int, float)):
                        elapsed = time.monotonic() - started
                        print(
                            f"[{_ts()}] ... {agent} still running | elapsed={elapsed:.1f}s",
                            file=sys.stdout,
                            flush=True,
                        )

            t = threading.Thread(target=_runner, name=f"watchdog-{agent}", daemon=True)
            t.start()

        def _stop_watchdog(agent: str) -> None:
            stop = step_watchdog_stop.pop(agent, None)
            if stop:
                stop.set()

        def on_start(agent: str, meta: dict[str, Any]) -> None:
            nonlocal parallel_pending_branches
            step_started_at[agent] = time.monotonic()
            model = str(meta.get("model") or "?")
            timeout_s = meta.get("timeout_s")
            timeout_label = f"{timeout_s}s" if isinstance(timeout_s, (int, float)) else "?"
            print(
                f"[{_ts()}] START {agent} | model={model} timeout={timeout_label}",
                file=sys.stdout,
                flush=True,
            )
            if agent == "__parallel__":
                branches = meta.get("branches")
                if isinstance(branches, list):
                    parallel_pending_branches = len(branches)
            _start_watchdog(agent)
            if dashboard:
                dashboard.set_agent_run(agent, meta)

        def on_done(agent: str, res: dict) -> None:
            nonlocal parallel_pending_branches
            started = step_started_at.pop(agent, None)
            elapsed = (
                f"{time.monotonic() - started:.1f}s"
                if isinstance(started, (int, float))
                else "?"
            )
            status = "OK" if bool(res.get("ok")) else "ERR"
            err = str(res.get("error") or "").strip()
            suffix = f" | {err[:180]}" if err else ""
            print(
                f"[{_ts()}] DONE  {agent} | status={status} elapsed={elapsed}{suffix}",
                file=sys.stdout,
                flush=True,
            )
            _stop_watchdog(agent)
            # In parallel-first mode we start "__parallel__" once and receive done
            # callbacks for each branch agent separately.
            if agent in ("researcher", "skeptic") and "__parallel__" in step_watchdog_stop:
                if parallel_pending_branches > 0:
                    parallel_pending_branches -= 1
                if parallel_pending_branches <= 0:
                    _stop_watchdog("__parallel__")
                    step_started_at.pop("__parallel__", None)
            if dashboard:
                dashboard.record_result(agent, res)

            if echo_thinking and eff_thinking:
                tip = res.get("_thinking_preview")
                if isinstance(tip, str) and tip.strip():
                    fp = session_path / f"{agent}_thinking.txt"
                    cap = 9000
                    body = tip.strip()
                    if len(body) > cap:
                        body = body[:cap] + "\n… (truncated; see " + fp.name + ")"
                    print(
                        f"\n--- Thinking · {agent} (full file: {fp.name}) ---\n{body}\n",
                        file=sys.stderr,
                        flush=True,
                    )

        if not short_circuited:
            acfg = cfg.get("adaptive") or {}
            if not isinstance(acfg, dict):
                acfg = {}
            cfg_adaptive_enabled = bool(acfg.get("enabled", False))
            eff_router = str(acfg.get("router", "off")).strip().lower()
            if args.adaptive is not None:
                if args.adaptive == "off":
                    eff_router = "off"
                else:
                    eff_router = str(args.adaptive)
            run_adaptive = cfg_adaptive_enabled and eff_router not in ("off", "")

            if run_adaptive:
                max_digits = int(acfg.get("max_trivial_add_digits", 6))
                h = adaptive.classify_heuristic(question, max_digits)
                final_tier = "T2"
                final_reason = "unclassified"
                slm_reason: str | None = None
                if h.tier == "T0":
                    final_tier = "T0"
                    final_reason = h.reason
                elif h.tier is None:
                    if eff_router == "heuristic":
                        final_tier = "T2"
                        final_reason = h.reason
                    elif eff_router == "heuristic_then_slm":
                        slm = adaptive.classify_slm(question, cfg, session_path)
                        final_tier = slm.tier if slm.tier in ("T1", "T2") else "T2"
                        final_reason = slm.reason
                        slm_reason = slm.reason
                adaptive_tier = final_tier
                adaptive_meta = {
                    "reason": final_reason,
                    "router": eff_router,
                    "heuristic_reason": h.reason,
                    "classify_slm_reason": slm_reason,
                }
                if final_tier == "T0" and h.t0_a is not None:
                    adaptive_meta["t0_a"] = h.t0_a
                    adaptive_meta["t0_b"] = h.t0_b
                    adaptive_meta["t0_sum"] = h.t0_sum

            if dashboard:
                if run_adaptive:
                    dashboard.set_adaptive_route(adaptive_tier, adaptive_meta)
                else:
                    dashboard.set_adaptive_route(None, None)

            summary = run_pipeline(
                root=root,
                session_path=session_path,
                question=question,
                cfg=cfg,
                prompts_dir=prompts_dir,
                manual_pause=manual_pause if args.manual else None,
                parallel_initial=parallel,
                on_agent_start=on_start,
                on_agent_done=on_done,
                show_thinking=eff_thinking,
                prior_reference=prior_reference,
                adaptive_tier=adaptive_tier,
                adaptive_meta=adaptive_meta,
            )
        else:
            print(
                f"\n[session reuse] short_circuit — no Gemini calls (expert mode). "
                f"Prior: `{matched_name}` (wording ~{matched_sim_log:.0%}, shared words ~{matched_word_log:.0%}; "
                f"answer age ~{matched_age_days:.1f} days).\n"
                "If anything important changed since that run, re-run with reuse off or use inject only.\n",
                file=sys.stdout,
            )
            try:
                (session_path / "reuse_provenance.json").write_text(
                    json.dumps(
                        {
                            "mode": "short_circuit",
                            "prior_session": matched_name,
                            "similarity": matched_sim_log,
                            "word_overlap": matched_word_log,
                            "prior_session_age_days": matched_age_days,
                            "prior_question_excerpt": prior_q[:2000],
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            synth_txt = str((summary.get("outputs") or {}).get("synthesizer", "") or "")
            scratchpad.append_agent_section(
                session_path,
                "short_circuit_reuse",
                (
                    f"_(No live agents ran. Reused integrated answer from `{matched_name}`; "
                    f"wording ~{matched_sim_log:.0%}, shared words ~{matched_word_log:.0%}; "
                    f"answer ~{matched_age_days:.1f} days old.)_\n\n"
                )
                + synth_txt.strip()[:80000],
            )
            (session_path / "disagreements.json").write_text(
                json.dumps(summary.get("disagreement") or {}, indent=2),
                encoding="utf-8",
            )

        if log_reuse == "inject" and prior_reference and matched_name:
            print(
                f"\n[session reuse] inject — prior `{matched_name}` added to researcher only "
                f"(wording ~{matched_sim_log:.0%}, shared words ~{matched_word_log:.0%}; "
                f"that answer is ~{matched_age_days:.1f} days old). Full debate still runs.\n",
                file=sys.stdout,
            )
            try:
                (session_path / "reuse_provenance.json").write_text(
                    json.dumps(
                        {
                            "mode": "inject",
                            "prior_session": matched_name,
                            "similarity": matched_sim_log,
                            "word_overlap": matched_word_log,
                            "prior_session_age_days": matched_age_days,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass

        mu = summary.get("models_used")
        if isinstance(mu, dict):
            (session_path / "models_used.json").write_text(
                json.dumps(mu, indent=2),
                encoding="utf-8",
            )

        reporting.write_report(
            session_path=session_path,
            question=question,
            outputs=summary["outputs"],
            disagreement=summary["disagreement"],
            scores=summary["scores"],
            synthesizer_confidence=summary.get("synthesizer_confidence"),
            errors=summary.get("errors") or [],
            models_used=summary.get("models_used"),
            thinking_outputs=summary.get("thinking_outputs") or {},
            show_thinking=bool(summary.get("show_thinking")),
        )

        _write_synthesizer_public(session_path, summary.get("outputs") or {})

        if dashboard:
            dashboard.set_phase("done", "reporting")
            dashboard.set_preview(f"Session: {session_path}")

    finally:
        # Stop any stdout watchdog threads (dashboard mode skips these threads entirely).
        for ev in list(step_watchdog_stop.values()):
            ev.set()
        step_watchdog_stop.clear()
        if dash_ctx is not None and dashboard is not None:
            dashboard.__exit__(None, None, None)

    _print_and_save_final_answer(session_path, question, summary)

    session_cache.append_topic_log(
        sessions_root,
        session_id=session_path.name,
        norm_question=session_cache.normalize_question(question),
        reuse_mode=log_reuse,
        matched_session=matched_name,
        similarity=matched_sim_log,
        word_overlap=matched_word_log,
        age_days=matched_age_days,
    )

    print(f"\nSession written to: {session_path}")
    print(
        "  - final_answer.txt (same text as the FINAL ANSWER block above)\n"
        "  - synthesizer_public.txt (for future session reuse)\n"
        "  - reuse_provenance.json (when reuse matched)\n"
        "  - sessions/topic_log.jsonl (append-only run index)\n"
        "  - report.md\n  - transcript.md\n  - audit.json\n  - disagreements.json\n"
        "  - models_used.json\n  - scratchpad.md\n  - shared_facts.md\n"
        "  - optional *_thinking.txt when thinking mode is on"
    )
    errs = summary.get("errors") or []
    if errs:
        blob = "\n".join(str(e) for e in errs)
        if "GEMINI_API_KEY" in blob or "GOOGLE_API_KEY" in blob:
            print(
                "\n[GemmaPie] Model steps failed: the Gemini CLI reported missing API credentials.\n"
                "  Set GEMINI_API_KEY or GOOGLE_API_KEY in your environment (see .env.example), or authenticate\n"
                "  per https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html\n",
                file=sys.stderr,
            )
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
