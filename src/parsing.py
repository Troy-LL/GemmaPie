from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_CONF_RE = re.compile(r"Confidence:\s*(\d{1,2})\s*/\s*10", re.IGNORECASE)
_UNCERT_RE = re.compile(r"Key uncertainty:\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL)


@dataclass
class ParsedTail:
    confidence: int | None
    uncertainty: str | None


def parse_response_tail(text: str) -> ParsedTail:
    c = None
    m = _CONF_RE.search(text)
    if m:
        try:
            v = int(m.group(1))
            if 0 <= v <= 10:
                c = v
        except ValueError:
            c = None
    u = None
    m2 = _UNCERT_RE.search(text)
    if m2:
        u = m2.group(1).strip()
    return ParsedTail(confidence=c, uncertainty=u)


@dataclass
class ThinkingSplit:
    """Separates optional pre-answer reasoning from the public agent reply."""

    thinking: str
    public: str


def split_thinking_body(text: str) -> ThinkingSplit:
    """
    Prefer `<thinking>...</thinking>`; else `## Scratch reasoning` up to the next
    major heading (`## `), `---`, or `Confidence:` line. If no block, public == full text.
    """
    text = text or ""
    m = re.search(r"<thinking>([\s\S]*?)</thinking>", text, re.IGNORECASE)
    if m:
        thinking = m.group(1).strip()
        public = re.sub(r"<thinking>[\s\S]*?</thinking>\s*", "", text, count=1, flags=re.IGNORECASE).strip()
        return ThinkingSplit(thinking=thinking, public=public)

    m2 = re.search(
        r"##\s*Scratch\s*reasoning\s*\n([\s\S]*?)(?=\n## |\n---\s*\n|\nConfidence:\s*\d)",
        text,
        re.IGNORECASE,
    )
    if m2:
        thinking = m2.group(1).strip()
        public = re.sub(
            r"##\s*Scratch\s*reasoning\s*\n[\s\S]*?(?=\n## |\n---\s*\n|\nConfidence:\s*\d)",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        return ThinkingSplit(thinking=thinking, public=public)

    return ThinkingSplit(thinking="", public=text.strip())


def extract_native_thoughts_from_raw(raw: Any) -> str:
    """Best-effort: pull string thought fields from headless JSON if present."""
    if not isinstance(raw, dict):
        return ""
    for key in ("thoughts", "thought", "reasoning", "thinking"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    stats = raw.get("stats")
    if isinstance(stats, dict):
        for key in ("thoughts", "thought", "reasoning"):
            val = stats.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def extract_claims_json(text: str) -> list[dict] | None:
    """
    Find ```json ... ``` after ## Claims (Synthesizer contract).
    """
    if "## Claims" not in text and "## claims" not in text.lower():
        return None
    fence = re.search(r"##\s*Claims\s*```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if not fence:
        fence = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if not fence:
        return None
    raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return None


def strip_synthesizer_claims_block(text: str) -> str:
    """
    Remove the Synthesizer's mandatory ``## Claims`` + fenced JSON for human-readable display.
    Keeps the narrative and the trailing Confidence / Key uncertainty lines when present.
    """
    t = (text or "").strip()
    if not t:
        return t
    t2 = re.sub(
        r"\n##\s*Claims\s*\n?```json\s*[\s\S]*?```\s*",
        "\n\n",
        t,
        count=1,
        flags=re.IGNORECASE,
    )
    if t2 != t:
        return t2.strip()
    t3 = re.sub(
        r"\n##\s*Claims\s*[\s\S]*?(?=\nConfidence:\s*)",
        "\n\n",
        t,
        count=1,
        flags=re.IGNORECASE,
    )
    return t3.strip()
