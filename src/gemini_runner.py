from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def gemini_executable() -> str:
    """Return `gemini` if on PATH, else the name for error messages."""
    found = shutil.which("gemini")
    return found or "gemini"


def run_gemini(
    prompt: str,
    *,
    model: str,
    timeout: float,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """
    Invoke the Gemini CLI in headless JSON mode.

    Uses list arguments (no shell) for Windows safety. Prefer passing a small
    ``cwd`` (e.g. the session directory) so the CLI does not index the whole repo.

    Returns a dict with keys:
      - ok (bool)
      - text (str) — model response text when available
      - raw (dict | None) — parsed JSON object when stdout JSON parsed
      - stderr (str)
      - error (str | None) — human-readable failure summary
      - returncode (int | None)
    """
    exe = gemini_executable()
    if exe == "gemini" and shutil.which("gemini") is None:
        return {
            "ok": False,
            "text": "",
            "raw": None,
            "stderr": "",
            "error": "Executable `gemini` not found on PATH. Install the Gemini CLI and retry.",
            "returncode": None,
        }

    cmd: list[str] = [
        exe,
        "-p",
        prompt,
        "--output-format",
        "json",
        "-m",
        model,
    ]

    popen_kw: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": str(cwd) if cwd else None,
        "shell": False,
    }
    if sys.platform == "win32":
        # Avoid an extra console window; reduces odd interactions with Rich Live.
        popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(cmd, **popen_kw)
        assert proc.stdout is not None and proc.stderr is not None
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except Exception:
                stdout, stderr = ("", "")
        else:
            stdout, stderr = ("", "")
        err = f"Timed out after {timeout}s waiting for `gemini`."
        return {
            "ok": False,
            "text": "",
            "raw": None,
            "stderr": (stderr or "") if isinstance(stderr, str) else "",
            "error": err,
            "returncode": None,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "text": "",
            "raw": None,
            "stderr": "",
            "error": "Executable `gemini` not found on PATH.",
            "returncode": None,
        }
    except KeyboardInterrupt:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.communicate(timeout=15)
            except Exception:
                pass
        raise

    stderr = stderr or ""
    stdout = stdout or ""
    proc_return = proc.returncode if proc is not None else None

    raw: dict[str, Any] | None = None
    if stdout.strip():
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError:
            raw = None

    text = ""
    if isinstance(raw, dict):
        err_obj = raw.get("error")
        if isinstance(err_obj, dict) and err_obj.get("message"):
            # CLI-level error inside JSON
            pass
        resp = raw.get("response")
        if isinstance(resp, str):
            text = resp
        elif resp is not None:
            text = str(resp)
    elif stdout.strip():
        text = stdout.strip()

    ok = proc_return == 0 and bool(text)
    error: str | None = None
    if proc_return is not None and proc_return != 0:
        error = f"`gemini` exited with code {proc_return}."
        if proc_return == 130:
            error += (
                " (130 often means the process was interrupted, e.g. SIGINT / Ctrl+C, or the CLI aborted.)"
            )
    elif not text:
        error = "Empty model response (no parseable JSON `response` field and empty stdout)."
    if stderr.strip() and not ok:
        error = (error or "") + (" " if error else "") + f"stderr: {stderr.strip()[:2000]}"
        if "ripgrep" in stderr.lower():
            error += (
                " Note: Gemini CLI uses Ripgrep for some tools; install `rg` on PATH or see "
                "Gemini CLI docs / GitHub issues for Windows ripgrep detection."
            )

    if isinstance(raw, dict):
        inner_err = raw.get("error")
        if isinstance(inner_err, dict) and inner_err.get("message"):
            ok = False
            error = str(inner_err.get("message"))

    return {
        "ok": ok,
        "text": text,
        "raw": raw,
        "stderr": stderr,
        "error": error,
        "returncode": proc_return,
    }
