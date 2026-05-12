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
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from src import reporting, scratchpad, session_cache
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
    default_model = str(cfg.get("model", "gemini-2.0-flash"))
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
    env_on = os.environ.get("SHOW_AGENT_THINKING", "").strip().lower() in ("1", "true", "yes")
    cfg_thinking = bool((cfg.get("thinking") or {}).get("enabled", False))
    if args.no_thinking:
        eff_thinking = False
    elif args.show_thinking:
        eff_thinking = True
    else:
        eff_thinking = cfg_thinking or env_on

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

    try:

        def on_start(agent: str, meta: dict[str, Any]) -> None:
            if dashboard:
                dashboard.set_agent_run(agent, meta)

        def on_done(agent: str, res: dict) -> None:
            if dashboard:
                dashboard.record_result(agent, res)

        if not short_circuited:
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
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
