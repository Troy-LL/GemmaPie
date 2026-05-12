# Demo recording checklist (approx. 3 minutes)

Use a **contested** question where roles visibly diverge (example: *"Is nuclear energy safe?"* or *"Does social media harm teenagers?"*).

1. **Intro (15–20s):** State the one-liner: multi-agent Gemma/Gemini pipeline where disagreement is visible and logged.
2. **Run (45–60s):** In a terminal, show `python orchestrate.py "…"` (or `--parallel` if you want a faster first beat). Prefer a terminal font/size readable on video.
3. **Live surface (20–30s):** Point at the Rich panel (agent name + OK/ERR line, optional thinking preview). If the CLI is not available in the recording environment, show a **prior** `sessions/...` folder from a successful run instead, and narrate the same flow.
4. **Models (30–45s):** Open `models_used.json`; explain **L / M / S** tiers (see `agent_configs.md`) and why reviewer/synthesizer use heavier ids than skeptic.
5. **Thinking (30–45s):** Re-run or show a session with `python orchestrate.py --show-thinking "…"`. Open one `*_thinking.txt` and the **Thinking** sections in `report.md` / `transcript.md`.
6. **Disagreement (30–45s):** Open `disagreements.json` and explain `high_disagreement` / spread vs threshold; tie it to why a recursive Researcher/Skeptic round appeared or did not.
7. **Transparency (45–60s):** Open `report.md` (epistemic status) and skim `transcript.md`; show `audit.json` claims if the Synthesizer emitted the `## Claims` JSON block.
8. **Close (15s):** Safety & Trust + Education tracks: auditable reasoning + students watching agents disagree constructively.

Optional B-roll: two terminal panes—one running `--manual` for pedagogy.
