"""Rich terminal dashboard for live orchestration status."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


class Dashboard:
    def __init__(self) -> None:
        # Avoid Unicode spinner/braille on legacy Windows consoles (cp1252).
        self.console = Console(legacy_windows=False, emoji=False)
        self._live: Live | None = None
        self._phase = "init"
        self._agent = ""
        self._preview = ""
        self._run_start: float | None = None
        self._run_meta: dict[str, Any] = {}

    def _agent_label(self) -> str:
        if self._agent == "__parallel__":
            return "researcher + skeptic (parallel)"
        return self._agent or "-"

    def __enter__(self) -> "Dashboard":
        self._live = Live(self._render(), console=self.console, refresh_per_second=6)
        self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._live:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def set_phase(self, phase: str, agent: str = "") -> None:
        self._phase = phase
        self._agent = agent
        if phase == "done":
            self._run_start = None
            self._run_meta = {}
        self._refresh()

    def set_agent_run(self, agent: str, meta: dict[str, Any]) -> None:
        """Mark the start of a blocking Gemini CLI step (Rich Live re-renders ~6/s for elapsed time)."""
        self._phase = "running"
        self._agent = agent
        self._run_meta = dict(meta)
        self._run_start = time.monotonic()
        self._refresh()

    def set_preview(self, text: str) -> None:
        self._preview = (text or "").replace("\r", " ").strip()
        if len(self._preview) > 220:
            self._preview = self._preview[:217] + "..."
        self._refresh()

    def record_result(self, agent: str, res: dict[str, Any]) -> None:
        ok = bool(res.get("ok"))
        prev = "OK" if ok else f"ERR: {res.get('error') or 'unknown'}"
        tip = res.get("_thinking_preview")
        if isinstance(tip, str) and tip.strip():
            tip_s = tip.strip().replace("\n", " ")
            if len(tip_s) > 120:
                tip_s = tip_s[:117] + "..."
            prev = f"{prev} | thinking: {tip_s}"
        self.set_preview(f"{agent}: {prev}")
        self._refresh()

    def _activity_lines(self) -> list[Text]:
        """Human-readable lines for what is blocking (subprocess has no token stream)."""
        lines: list[Text] = []
        if self._run_start is None:
            return lines

        elapsed = int(time.monotonic() - self._run_start)
        lines.append(
            Text(
                "Process: local `gemini` subprocess (headless JSON on stdout; blocks until complete).",
                style="dim",
            )
        )
        lines.append(Text(f"Elapsed: {elapsed}s", style="yellow"))

        meta = self._run_meta
        st = bool(meta.get("show_thinking"))
        thinking_note = (
            "Thinking mode on: traces are parsed after this step (CLI does not stream thinking live). "
            "Watch elapsed time and the line below when the step finishes."
            if st
            else "Thinking mode off: use --show-thinking or thinking.enabled in config for traces."
        )
        lines.append(Text(thinking_note, style="dim"))

        if meta.get("parallel") and isinstance(meta.get("branches"), list):
            lines.append(
                Text(
                    "Note: preview may update when one branch finishes while the other is still running.",
                    style="dim",
                )
            )
            lines.append(Text("Parallel first turn (two subprocesses):", style="bold"))
            for br in meta["branches"]:
                if not isinstance(br, dict):
                    continue
                a = str(br.get("agent", "?"))
                m = str(br.get("model", "?"))
                pc = br.get("prompt_chars")
                to = br.get("timeout_s", "?")
                pc_s = f", prompt ~{pc} chars" if isinstance(pc, int) else ""
                lines.append(Text(f"  - {a}: -m {m}, timeout {to}s{pc_s}", style="cyan"))
            return lines

        if self._agent and self._agent != "__parallel__":
            m = str(meta.get("model", "?"))
            to = meta.get("timeout_s", "?")
            pc = meta.get("prompt_chars")
            pc_s = f", composed prompt ~{pc} chars" if isinstance(pc, int) else ""
            lines.append(Text(f"Model: -m {m}, timeout budget {to}s{pc_s}", style="cyan"))

        return lines

    def _render(self) -> Panel:
        phase_line = Text(f"Phase: {self._phase}", style="dim")
        header = Text.assemble(
            ("Gemma 4 Distributed Cognition", "bold"),
            "\n",
            (
                f"Agent: {self._agent_label()}",
                "cyan",
            ),
        )
        activity = self._activity_lines()
        body_children: list[Any] = [header, phase_line, *activity]
        if self._preview:
            body_children.append(Text(self._preview))
        body = Group(*body_children)
        return Panel(body, title="Live", border_style="blue")

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())
