from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_RL_LOCK = threading.Lock()
_SEM: threading.Semaphore | None = None
_last_popen_monotonic: float = 0.0
_min_interval_s: float = 0.0
_max_retries_rl: int = 0
_retry_base_s: float = 4.0
_retry_cap_s: float = 120.0


def apply_rate_limit_settings(rl: dict[str, Any] | None) -> None:
    """
    Load pacing from ``config.yaml`` key ``rate_limit`` (or ``None`` for defaults).

    Defaults preserve prior behavior: no spacing, up to two concurrent ``gemini``
    processes (for parallel Researcher+Skeptic), no retries on quota errors.
    """
    global _SEM, _min_interval_s, _max_retries_rl, _retry_base_s, _retry_cap_s
    defaults = {
        "min_interval_s": 0.0,
        "max_concurrent": 2,
        "max_retries_on_rate_limit": 0,
        "retry_backoff_base_s": 4.0,
        "retry_backoff_max_s": 120.0,
    }
    if not isinstance(rl, dict):
        rl = {}
    merged = dict(defaults)
    for key in defaults:
        if key in rl and rl[key] is not None:
            merged[key] = rl[key]

    with _RL_LOCK:
        _min_interval_s = max(0.0, float(merged["min_interval_s"] or 0))
        _max_retries_rl = max(0, int(merged["max_retries_on_rate_limit"] or 0))
        _retry_base_s = max(0.25, float(merged["retry_backoff_base_s"] or 4))
        _retry_cap_s = max(1.0, float(merged["retry_backoff_max_s"] or 120))
        mc = max(1, int(merged["max_concurrent"] or 2))
        _SEM = threading.Semaphore(mc)


def _ensure_sem() -> threading.Semaphore:
    if _SEM is None:
        apply_rate_limit_settings(None)
    assert _SEM is not None
    return _SEM


def _throttle_unlocked() -> None:
    global _last_popen_monotonic
    if _min_interval_s <= 0:
        _last_popen_monotonic = time.monotonic()
        return
    now = time.monotonic()
    wait = _last_popen_monotonic + _min_interval_s - now
    if wait > 0:
        time.sleep(wait)
    _last_popen_monotonic = time.monotonic()


def _is_rate_limit_failure(res: dict[str, Any]) -> bool:
    if res.get("ok"):
        return False
    blob = (
        (res.get("error") or "")
        + "\n"
        + (res.get("stderr") or "")
        + "\n"
    ).lower()
    needles = (
        "429",
        "quota",
        "rate limit",
        "too many requests",
        "resource exhausted",
        "resourceexhausted",
        "terminalquota",
        "throttl",
    )
    return any(n in blob for n in needles)


def _run_gemini_subprocess(
    prompt: str,
    *,
    model: str,
    timeout: float,
    cwd: Path | None,
    exe: str,
) -> dict[str, Any]:
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

    Optional global pacing from ``apply_rate_limit_settings`` (``min_interval_s``,
    ``max_concurrent``, retries on 429/quota-style failures).

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

    sem = _ensure_sem()
    max_tries = 1 + _max_retries_rl
    last: dict[str, Any] | None = None

    for attempt in range(max_tries):
        sem.acquire()
        try:
            with _RL_LOCK:
                _throttle_unlocked()
            last = _run_gemini_subprocess(
                prompt, model=model, timeout=timeout, cwd=cwd, exe=exe
            )
        finally:
            sem.release()

        if last.get("ok") or not _is_rate_limit_failure(last) or attempt >= _max_retries_rl:
            if attempt > 0 and last.get("ok"):
                last = {
                    **last,
                    "_rate_limit_retries": attempt,
                }
            return last

        # Exponential backoff with jitter before retrying quota / throttle errors.
        backoff = min(_retry_cap_s, _retry_base_s * (2**attempt))
        backoff += random.uniform(0, min(2.0, backoff * 0.1))
        time.sleep(backoff)

    return last or {
        "ok": False,
        "text": "",
        "raw": None,
        "stderr": "",
        "error": "Internal error: rate-limit retry loop exited empty.",
        "returncode": None,
    }
