# Gemma 4 Distributed Cognition System — Implementation Plan

**Last Updated:** May 13, 2026 (heterogeneous model tiers + thinking-trace transparency)
**Status:** In Progress
**Hackathon:** Gemma 4 Impact Challenge
**Primary Track:** Safety & Trust
**Secondary Track:** Future of Education

---

## Overview

This document outlines the step-by-step implementation plan for the Gemma 4 Distributed Cognition System — a multi-agent architecture where multiple **Gemma-family** models run as separate **Gemini CLI invocations**, each with role-specific system prompts and (where supported) distinct sampling profiles. **Model capacity is heterogeneous:** more capable Gemma variants handle integration and evaluation; lighter variants handle faster or narrower passes. **Transparency is first-class:** an optional mode surfaces each agent’s *thinking trace* (native model thoughts when the API/CLI exposes them, and/or structured reasoning blocks in the transcript) so inter-agent communication is as little a black box as the stack allows. The system reduces sycophancy, surfaces disagreement, and makes AI reasoning explainable and auditable.

**Track Rationale:**

- **Safety & Trust (Primary):** The core thesis — reducing agreement bias, exposing reasoning chains, and producing explainable, auditable AI outputs — is a direct Safety & Trust contribution. Every design decision (critique loops, confidence scoring, disagreement flags, audit trails) serves transparency and reliability.
- **Future of Education (Secondary):** The "debate mode" interface lets students observe AI agents genuinely disagreeing and revising conclusions in real time. This teaches critical thinking and epistemic humility in a way a single chatbot cannot.

**Anchor Demo Scenario:** A student asks a contested question (e.g., *"Is nuclear energy safe?"* or *"Does social media harm teenagers?"*). The system shows multiple Gemma agents (optionally **different model tiers** per role) independently researching, critiquing each other, flagging disagreements, and synthesizing a transparent final answer — with the **full reasoning chain** and optional **per-agent thinking traces** visible.

## Implementation status (repository — v2 sweep)

| Area | Status |
|------|--------|
| `orchestrate.py` CLI (`--manual`, `--parallel`, `--show-thinking` / `--no-thinking`, `--no-dashboard`) | Done |
| Session layout (`sessions/session_*`, scratchpad, `shared_facts.md`, `models_used.json`) | Done |
| Pipeline: Researcher → Skeptic → Contrarian → Reviewer → [S2→R2 if high disagreement] → Synthesizer | Done |
| Per-agent `-m` via `config.yaml` `models:` + `model_for()` | Done |
| Disagreement log, confidence parsing, `report.md` / `transcript.md` / `audit.json` | Done |
| Thinking: `<thinking>` split, public-only context for downstream agents, `*_thinking.txt`, reporting sections | Done |
| Streamlit UI | Not planned for v1 (stretch) |

---

## Approach: Gemini CLI Multi-Instance Architecture

Instead of building a custom LLM runtime, the system uses the **Gemini CLI** to run multiple **Gemma-family** invocations in parallel terminal windows (or managed subprocesses). Each invocation is a separate agent with:

- a distinct **system prompt** defining its role and personality,
- a **chosen model id** (tier **L / M / S**) appropriate to that role—not necessarily the same checkpoint for every agent,
- different **model parameters** (temperature, top-p) tuned to its role when the CLI exposes them (otherwise prompt-embedded behavior),
- and a **shared scratchpad file or message bus** for inter-agent communication.

This keeps the project focused on the novel organizational intelligence layer rather than infrastructure.

**Agent sampling intent (temperature / top-p):** documented per role in [`agent_configs.md`](agent_configs.md) — prompt-embedded only; the CLI does not expose per-call sampling flags.

### Inter-Agent Communication Options

- **Shared markdown scratchpad** (simplest): agents read/write to a shared `.md` file polled on a timer — start here
- **SQLite message table** (lightweight): agents insert/query rows as a message bus — upgrade if race conditions appear
- **Named pipes or local socket** (most robust): real-time message passing between CLI processes — stretch goal

### Heterogeneous Gemma model tiers (planned)

Not every role needs the same capacity. The orchestrator should map **each agent to its own `-m` / model id** (in config), using **larger / stronger Gemma variants** where errors are most costly and **smaller / faster variants** where the task is narrower—without defaulting to trivially tiny models if quality collapses.

| Tier | Typical roles | Rationale |
|------|-----------------|-----------|
| **Tier L (largest)** | Synthesizer; optionally Reviewer | Final user-facing integration and careful weighing of conflict; evaluation benefits from headroom. |
| **Tier M (mid)** | Researcher; Contrarian | Long-form analysis and structured dissent; needs solid reasoning but not always the heaviest checkpoint. |
| **Tier S (smaller, not “tiny”)** | Skeptic (especially parallel first pass); optional scout / pre-read steps | Fast challenge framing, assumption surfacing, or breadth-first passes; still large enough to avoid collapse into gibberish. |

Exact **model id strings** for Gemma 3 / Gemma 4 and specialty checkpoints are catalogued in [`agent_configs.md`](agent_configs.md) (May 2026); map each role under `config.yaml` → `models:` and keep a **single-model fallback** (`model:`) for demos when a tier is unavailable.

### Transparency: surfacing agent “thinking” (anti–black-box)

**Goal:** judges, educators, and students should see *why* each layer moved—not only the final chat-style answer.

Planned mechanisms (combine as available):

1. **CLI / JSON metadata:** when headless JSON includes separate fields for **thoughts**, **reasoning**, or token/stats blocks, persist them per agent under `sessions/.../` (e.g. `*_raw.json` already; extend with a dedicated `thinking/` or `traces.md` slice).
2. **Prompt contract:** require an explicit machine-parsable block (e.g. `<thinking>...</thinking>` or `## Scratch reasoning`) *before* the public answer, so transparency does not depend solely on proprietary thought APIs.
3. **Orchestrator flag:** e.g. `--show-thinking` (or env `SHOW_AGENT_THINKING=1`) that streams or echoes those blocks to the terminal dashboard and includes them in `report.md` / `transcript.md` (with a clear visual separation from “final” agent output).
4. **Education mode:** default-on for classroom demos; can be off for minimal logs in CI.

**Non-goals:** pretending we have chain-of-thought access when the model/API does not provide it; in that case, only (2) + structured disagreement logs apply.

---

## Phase 1 — Environment Setup

**Goal:** Get multiple Gemini CLI Gemma instances running and communicating.

**Tasks:**

- [ ] Install Gemini CLI and authenticate (see [docs/RESOURCES.md](docs/RESOURCES.md))
- [ ] Confirm each planned **L / M / S** model id works on your account (`gemini` / API model list)
- [x] Role prompt files under `prompts/` (including `<thinking>` discipline for transparency)
- [x] Orchestrated pipeline (`orchestrate.py` + `src/pipeline.py`) — no separate polling script
- [x] Session scratchpad (`sessions/.../scratchpad.md`) read/write via orchestrator
- [x] End-to-end: sequential agent handoff via shared context + scratchpad

**Deliverable:** Two or more agents exchanging context through the orchestrated session (met).

---

## Phase 2 — Agent Role Prompts & Parameter Tuning

**Goal:** Make each agent behave distinctly and produce meaningfully different outputs.

**Tasks:**

- [x] Draft system prompts for all 5 roles (+ thinking trace instructions)
- [ ] Run regression prompts on contested questions; tune wording as needed
- [x] Sampling intent documented (prompt-only) in `agent_configs.md`
- [x] Anti-sycophancy on Skeptic / Contrarian
- [x] Footer: `Confidence: X/10` and `Key uncertainty: ...`
- [x] Per-agent model map in `agent_configs.md` + `config.yaml` `models:`

**Deliverable:** Differentiated personas + documented heterogeneous model map.

---

## Phase 3 — Critique & Disagreement Pipeline

**Goal:** Build the core anti-sycophancy workflow.

**Tasks:**

- [x] Critique sequence (Researcher → Skeptic → Contrarian → Reviewer → Synthesizer)
- [x] Disagreement from confidence spread vs threshold → `disagreements.json`
- [x] Recursive **Skeptic round 2 → Researcher round 2** when high disagreement, then Synthesizer
- [ ] Regression on 3+ contested questions (manual QA)

**Deliverable:** End-to-end critique pipeline with disagreement log (met in code).

---

## Phase 4 — Shared Memory & Context Management

**Goal:** Give agents access to prior context without collapsing into groupthink.

**Tasks:**

- [x] Role-based context isolation (`src/context.py` — `build_context_for`)
- [x] `shared_facts.md` + `SHARED_FACT:` merge from Researcher outputs
- [x] Character budget trimming (`max_chars`)
- [x] Session IDs under `sessions/session_YYYYMMDD_HHMMSS/`
- [x] Downstream agents receive **public** text only (thinking tags stripped for inter-agent context)

**Deliverable:** Selective context sharing (met).

---

## Phase 5 — Orchestration Script

**Goal:** Automate the full multi-agent pipeline so it runs with one command.

**Tasks:**

- [x] `orchestrate.py`: question, `--config`, `--manual`, `--parallel`, `--show-thinking` / `--no-thinking`, `--no-dashboard`
- [x] Session artifacts including `models_used.json`
- [x] Per-agent `-m` from config
- [x] CLI errors collected; non-zero exit when any step errors (`orchestrate.py`)
- [ ] Full dry run on anchor scenario in target environment

**Deliverable:** `python orchestrate.py "…"` produces a session folder (met).

---

## Phase 6 — Transparency & Explainability Output

**Goal:** Make the reasoning process legible.

**Tasks (shipped):**

- [x] `report.md`: question, per-agent body + confidence, disagreements, epistemic status, **per-agent `-m`**
- [x] `transcript.md` with optional **Thinking** sections when thinking mode on
- [x] `audit.json` + `meta.models_used`, claims from Synthesizer `## Claims`
- [x] Rich live dashboard; thinking preview line when enabled
- [x] `*_thinking.txt` per agent when thinking mode on; JSON `*_raw.json` on success

**Tasks (stretch):**

- [ ] Streamlit UI

**Deliverable:** Transparency report + dashboard + model disclosure + thinking path (met for terminal path).

---

## Phase 7 — Demo Polish & Submission

**Goal:** Package everything into a compelling, submittable project.

**Tasks:**

- [ ] Finalize anchor demo question
- [ ] Record ~3 min demo (show `models_used.json`, optional `--show-thinking`, disagreements, `report.md`)
- [x] README + `.env.example` + LICENSE + [docs/RESOURCES.md](docs/RESOURCES.md)
- [ ] Final hackathon write-up (both tracks)
- [ ] Submit

**Deliverable:** Documented, demonstrable project.

---

## Open Questions — Resolution Plan

| Question | Resolution Approach |
|---|---|
| How should disagreement be visualized? | Terminal: color-coded confidence scores via `rich`. Stretch: Streamlit confidence bar chart |
| Should leadership emerge dynamically? | Not for v1 — fixed sequence is simpler and more explainable for judges |
| What memory-sharing strategy should be used? | Shared `.md` scratchpad for hackathon; SQLite if race conditions appear |
| How should specialization evolve over time? | Out of scope for v1; note as future work in submission |
| Single vs tiered models if quota is tight? | Prefer tiered for thesis; document fallback to one shared id in `agent_configs.md` |

---

## Milestones & Timeline

| Milestone | Target |
|---|---|
| Phase 1 — CLI instances running & communicating | Day 1 |
| Phase 2 — Role prompts tuned, differentiation confirmed | Day 1–2 |
| Phase 3 — Critique pipeline end-to-end | Day 2–3 |
| Phase 4 — Memory & context management | Day 3–4 |
| Phase 5 — Orchestration script working | Day 4–5 |
| Phase 6 — Transparency output & dashboard | Day 5–6 |
| Phase 7 — Demo recorded, README done, submitted | Day 7 |

---

## Tech Stack

| Layer | Tool |
|---|---|
| LLM Runtime | Gemini CLI → **tiered Gemma / Gemini model ids per agent** (`-m` per invocation) |
| Agent Communication | Shared `.md` scratchpad (or SQLite message table) |
| Thinking / traces | Headless JSON fields + prompt-structured blocks + session artifacts (`report.md`, optional `thinking/` dumps) |
| Orchestration | Python (`orchestrate.py`) |
| Terminal UI | `rich` or `textual` |
| Stretch Web UI | Streamlit |
| Output Format | Markdown session reports + `audit.json` |
| Version Control | Git monorepo |

---

## Project Framing (for submission)

**One-liner:** A multi-agent Gemma system where AI disagreement is transparent, auditable, and educational.

**Safety & Trust:** Rather than hiding model uncertainty behind a confident single answer, this system surfaces disagreement, scores confidence per agent, and produces a full audit trail — making AI reasoning legible and trustworthy by design.

**Future of Education:** Students and educators can watch agents with genuinely different perspectives debate a contested question, observe how conclusions form and change under scrutiny, and develop their own critical thinking alongside the system.

---

## References

- Recursive Multi-Agent Systems: https://arxiv.org/pdf/2604.25917
- Diversity Collapse in Multi-Agent Systems: https://arxiv.org/pdf/2604.18005
- OneManCompany: https://arxiv.org/pdf/2604.22446
- HALO: https://github.com/context-labs/halo
