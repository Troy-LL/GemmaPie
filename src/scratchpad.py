from __future__ import annotations

from datetime import datetime
from pathlib import Path


def new_session_id() -> str:
    return datetime.now().strftime("session_%Y%m%d_%H%M%S")


def session_dir(root: Path, session_id: str) -> Path:
    return root / "sessions" / session_id


def ensure_session_layout(root: Path, session_id: str) -> Path:
    """Create `sessions/<id>/` with scratchpad and shared facts file."""
    sdir = session_dir(root, session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    scratch = sdir / "scratchpad.md"
    if not scratch.exists():
        scratch.write_text(
            "# Session scratchpad\n\n_Appended entries are chronological._\n\n",
            encoding="utf-8",
        )
    facts = sdir / "shared_facts.md"
    if not facts.exists():
        facts.write_text(
            "# Shared facts\n\n_Auto-merged lines prefixed with SHARED_FACT from Researcher._\n\n",
            encoding="utf-8",
        )
    return sdir


def read_scratchpad(session_path: Path) -> str:
    p = session_path / "scratchpad.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def append_agent_section(session_path: Path, agent: str, content: str) -> None:
    """Append a clearly delimited block for this agent."""
    path = session_path / "scratchpad.md"
    if not path.exists():
        path.write_text("# Session scratchpad\n\n", encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    agent_title = agent.strip().lower()
    block = (
        f"\n## {agent_title} @ {stamp}\n\n"
        f"{content.strip()}\n\n"
        f"---\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def read_shared_facts(session_path: Path) -> str:
    p = session_path / "shared_facts.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def merge_shared_facts_from_researcher(session_path: Path, researcher_text: str) -> None:
    """Append unique `SHARED_FACT:` lines from researcher output."""
    lines = [
        ln.strip()
        for ln in researcher_text.splitlines()
        if ln.strip().upper().startswith("SHARED_FACT:")
    ]
    if not lines:
        return
    path = session_path / "shared_facts.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    to_add: list[str] = []
    for ln in lines:
        if ln not in existing:
            to_add.append(ln)
    if not to_add:
        return
    with path.open("a", encoding="utf-8") as f:
        for ln in to_add:
            f.write(ln + "\n")
