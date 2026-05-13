"""Rich terminal dashboard for live orchestration status."""

from __future__ import annotations

import threading
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
        self._finished_step = ""
        self._thinking_agent = ""
        self._thinking_text = ""
        self._run_start: float | None = None
        self._run_meta: dict[str, Any] = {}
        self._adaptive_line = ""
        self._tick_stop: threading.Event | None = None
        self._tick_thread: threading.Thread | None = None

    @staticmethod
    def _clip_thinking(s: str, *, max_chars: int = 7200, max_lines: int = 42) -> str:
        """Keep the Live panel usable on small terminals; full text stays in *_thinking.txt."""
        lines = s.splitlines()
        if len(lines) > max_lines:
            s = "\n".join(lines[:max_lines]) + "\n… (more lines in *_thinking.txt)"
        if len(s) > max_chars:
            s = s[: max_chars].rstrip() + "\n… (truncated; see *_thinking.txt)"
        return s

    def _agent_label(self) -> str:
        if self._agent == "__parallel__":
            return "researcher + skeptic (parallel)"
        return self._agent or "-"

    def __enter__(self) -> "Dashboard":
        # Higher refresh rate so elapsed time moves visibly when `update()` is driven by the tick thread.
        self._live = Live(self._render(), console=self.console, refresh_per_second=12)
        self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self._stop_tick()
        if self._live:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def set_adaptive_route(self, tier: str | None, meta: dict[str, Any] | None) -> None:
        """Show adaptive tier + router output on the Live panel (non-black-box routing)."""
        if tier:
            r = ""
            if isinstance(meta, dict):
                rr = meta.get("router")
                fr = meta.get("reason")
                if rr:
                    r += f" router={rr}"
                if fr:
                    r += f" | {fr}"
            self._adaptive_line = f"Adaptive tier: {tier}{r}".strip()
        else:
            self._adaptive_line = "Adaptive routing: off for this run"
        self._refresh()

    def set_phase(self, phase: str, agent: str = "") -> None:
        self._phase = phase
        self._agent = agent
        if phase == "done":
            self._run_start = None
            self._run_meta = {}
            self._stop_tick()
        self._refresh()

    def set_agent_run(self, agent: str, meta: dict[str, Any]) -> None:
        """Mark the start of a blocking Gemini CLI step."""
        self._phase = "running"
        self._agent = agent
        self._run_meta = dict(meta)
        self._run_start = time.monotonic()
        self._start_tick()
        self._refresh()

    def set_preview(self, text: str) -> None:
        self._preview = (text or "").replace("\r", " ").strip()
        if len(self._preview) > 280:
            self._preview = self._preview[:277] + "..."
        self._refresh()

    def record_result(self, agent: str, res: dict[str, Any]) -> None:
        """Show step status plus a multi-line thinking panel when traces exist."""
        ok = bool(res.get("ok"))
        status = "OK" if ok else f"ERR: {res.get('error') or 'unknown'}"
        self._finished_step = f"{agent}: {status}"

        tip = res.get("_thinking_preview")
        if isinstance(tip, str) and tip.strip():
            self._thinking_agent = agent
            self._thinking_text = self._clip_thinking(tip.strip())
        else:
            self._thinking_agent = ""
            self._thinking_text = ""

        # Pause elapsed until the next agent starts (avoid runaway timer between steps).
        self._run_start = None
        self._phase = "between"
        self._stop_tick()
        self._refresh()

    def _start_tick(self) -> None:
        """Poll Live updates while a subprocess blocks so Elapsed and spinners actually move."""
        self._stop_tick()
        stop = threading.Event()
        self._tick_stop = stop

        def _loop() -> None:
            while not stop.wait(0.12):
                if self._run_start is None:
                    continue
                self._refresh()

        t = threading.Thread(target=_loop, name="dashboard-tick", daemon=True)
        self._tick_thread = t
        t.start()

    def _stop_tick(self) -> None:
        if self._tick_stop:
            self._tick_stop.set()
        self._tick_stop = None
        self._tick_thread = None

    def _activity_lines(self) -> list[Text]:
        """Human-readable lines for what is blocking (subprocess has no token stream)."""
        lines: list[Text] = []
        if self._run_start is None:
            return lines

        elapsed = time.monotonic() - self._run_start
        lines.append(
            Text(
                "Process: local `gemini` subprocess (headless JSON on stdout; blocks until complete).",
                style="dim",
            )
        )
        lines.append(Text(f"Elapsed: {elapsed:.1f}s", style="yellow"))

        meta = self._run_meta
        st = bool(meta.get("show_thinking"))
        if st:
            lines.append(
                Text(
                    "During this step you only see wait time — parsed thinking appears in the green panel "
                    "after the subprocess exits (Gemini CLI does not stream private reasoning live).",
                    style="dim",
                )
            )
        else:
            lines.append(
                Text(
                    "Thinking traces off — pass --show-thinking or set thinking.enabled in config.",
                    style="dim",
                )
            )

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
        route_lines: list[Text] = []
        if self._adaptive_line:
            route_lines.append(Text(self._adaptive_line, style="magenta"))

        activity = self._activity_lines()
        body_children: list[Any] = [header, phase_line, *route_lines, *activity]

        if self._finished_step:
            body_children.append(Text("Last finished step:", style="bold"))
            body_children.append(Text(self._finished_step))

        if self._thinking_text and self._thinking_agent:
            body_children.append(
                Panel(
                    Text(self._thinking_text, overflow="fold"),
                    title=f"Thinking · {self._thinking_agent}",
                    subtitle="Full trace: <agent>_thinking.txt in this session folder",
                    border_style="green",
                )
            )

        if self._preview:
            body_children.append(Text("Note:", style="bold"))
            body_children.append(Text(self._preview))

        body = Group(*body_children)
        return Panel(body, title="Live", border_style="blue")

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())
