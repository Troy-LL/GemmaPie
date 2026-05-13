# GemmaPie · Gemma 4 Distributed Cognition System

**Repository:** [github.com/Troy-LL/GemmaPie](https://github.com/Troy-LL/GemmaPie)

Multi-agent **peer research** orchestrated in Python around the **Google Gemini CLI** in headless JSON mode. Five roles (Researcher, Skeptic, Contrarian, Reviewer, Synthesizer) run sequentially—optionally with **parallel** Researcher+Skeptic on the first turn—share a session scratchpad, log **confidence disagreement**, optionally run a **one-step recursive** Researcher/Skeptic refinement, and emit **transparent session artifacts** (`report.md`, `transcript.md`, `audit.json`).

**Tracks (hackathon framing):**

- **Safety & Trust:** disagreement and per-agent confidence are surfaced, not smoothed away; session logs support auditability.
- **Future of Education:** `transcript.md` is readable as a debate trace for learners.

## Prerequisites

1. **Python 3.10+**
2. **Google Gemini CLI** installed and on `PATH` as `gemini`, authenticated per [Authentication](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html).
3. Model ids your account can run. Set **`model`** and **`models:`** in [`config.yaml`](config.yaml), which is the **source of truth** for shipped defaults. The repo ships a **heterogeneous Gemini 3.x preview** stack (Flash / Flash-Lite / Pro-class ids); see [`agent_configs.md`](agent_configs.md) and confirm names with **`gemini models list`** (preview ids change). Use **Gemini 2.5** ids and/or **`model_fallback_chain`** when a preview model 404s or you need a compatibility ladder. For **Gemma** tiers when listed, see the same doc.

### Model verification (smoke)

1. Run `gemini models list` (or your channel’s equivalent) and confirm each id you placed under `models:` appears.
2. If an id is rejected, follow the **VRAM / quota cost ladder** in [`agent_configs.md`](agent_configs.md).
3. Quick probe: `gemini -p "ping" --output-format json -m <your-id>` (adjust flags per your CLI `--help`).

### Thinking transparency

- **Config:** `thinking.enabled: true` in [`config.yaml`](config.yaml), **or** env `SHOW_AGENT_THINKING=1`, **or** CLI `--show-thinking`.
- **Disable for a run:** `--no-thinking`.
- When on, session folder includes `*_thinking.txt` and `report.md` / `transcript.md` include **Thinking** sections. Downstream agents still receive only **public** text (content outside `<thinking>`).

## Setup

```bash
cd "p:\Troy\Code\Side Projects\AI Testing"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy [`.env.example`](.env.example) to `.env` only if your workflow uses env files; the Gemini CLI may use its own auth flow.

## Run

```bash
python orchestrate.py "Is nuclear energy safe?"
```

**Manual stepping** (press Enter between agents):

```bash
python orchestrate.py --manual "Does social media harm teenagers?"
```

**Parallel first turn** — Researcher and Skeptic each see only the question (Skeptic does not see the Researcher draft yet); Contrarian onward are sequential and see both memos:

```bash
python orchestrate.py --parallel "Is nuclear energy safe?"
```

**API pacing** (optional `rate_limit` in [`config.yaml`](config.yaml)): spaces out `gemini` subprocess starts, caps concurrent calls (`max_concurrent: 1` runs Researcher then Skeptic even with `--parallel`, preserving parallel *prompt* semantics), and retries with exponential backoff when stderr looks like **429 / quota / throttle**. Defaults are tuned to be gentle on a single API key; set `min_interval_s: 0` and `max_concurrent: 2` to approximate the old burstier behavior.

**Adaptive tiers** (optional cost/latency routing: trivial add → T0, optional SLM router → T1 light path, else full T2; shipped defaults enable adaptive routing in [`config.yaml`](config.yaml)). Read [`docs/ADAPTIVE_TIERS.md`](docs/ADAPTIVE_TIERS.md), enable `adaptive.enabled` in [`config.yaml`](config.yaml), or override for one run:

```bash
python orchestrate.py --adaptive heuristic_then_slm "3 + 5"
```

**Plain logs** (no Rich live panel):

```bash
python orchestrate.py --no-dashboard "Your question here"
```

**Thinking traces** in reports and `*_thinking.txt` files:

```bash
python orchestrate.py --show-thinking "Is nuclear energy safe?"
```

**Knowledge-base context** (inject selected files/folders into agent context):

```bash
python orchestrate.py --kb docs --kb notes/strategy.md "Is nuclear energy safe?"
```

You can also set defaults in `config.yaml` under `knowledge_base` (`enabled`, `paths`, `include_extensions`, `max_chars`, `mode`). Use **`mode: lexical_v2`** to chunk files (markdown headings / txt paragraphs), dedupe overlapping passages, rank snippets against the **user question** with TF‑IDF, and fit them under `max_chars`; **`mode: legacy`** keeps the older whole-file ordering and truncation. When used, each session writes `knowledge_context.md` and `knowledge_sources.json` (legacy: file list; lexical_v2: adds `chunks` with scores and stable chunk ids).

To scope KB to specific agents only, set `knowledge_base.roles` (e.g. `["researcher", "synthesizer"]`). If omitted/empty, KB is injected for all agents.

**Model fallback chain** (if a role’s primary `-m` fails, retry with the same prompt using each id in `model_fallback_chain`):
```yaml
model_fallback_chain:
  - gemini-2.5-flash
  - gemini-2.5-flash-lite
```
Omit or use an empty list to disable; your per-role `models:` stay as the first choice.

## Session outputs

Each run creates `sessions/session_YYYYMMDD_HHMMSS/` containing:

| File | Purpose |
|------|---------|
| `adaptive_tier.json` | When adaptive routing runs: chosen tier, router, and classification reasons |
| `question.txt` | User question |
| `scratchpad.md` | Chronological agent outputs |
| `shared_facts.md` | Lines extracted from Researcher outputs prefixed with `SHARED_FACT:` |
| `disagreements.json` | Confidence spread analysis + structured entries |
| `models_used.json` | Resolved `-m` model id per role (including round-2 inheritance) |
| `model_resolution.json` | Per agent: **primary** vs **resolved** model id and ordered **`attempts`** when fallbacks run |
| `knowledge_context.md` / `knowledge_sources.json` | When KB context is enabled: injected reference text; JSON lists sources (`legacy`) or adds **`chunks`** with **`id`**, **`path`**, **`score`** (`lexical_v2`) |
| `*_raw.json` | Raw Gemini CLI JSON (per step) |
| `report.md` | Human-readable transparency report + epistemic status |
| `transcript.md` | Debate-style ordering of outputs |
| `audit.json` | Parsed `## Claims` JSON from the Synthesizer (when present); `meta.models_used` |
| `*_thinking.txt` | Per-agent reasoning trace when thinking mode is on |

When a step falls back to a non-primary model, the CLI prints a one-line **`[models] role: primary → resolved (fallback)`** summary after **Session written to:** (happy paths stay quiet).

Full link index: [`docs/RESOURCES.md`](docs/RESOURCES.md).

### Internals & future work

- **`src/pipeline.py`:** If it keeps growing, a natural split would be `pipeline_core.py` (invoke + disagreement) vs `pipeline_adaptive.py` (T0/T1/T2 orchestration). Not required today.
- **Knowledge base v2 (lexical):** Implemented via `knowledge_base.mode: lexical_v2` — optional future work includes **embedding retrieval** and **claim-level citations** wired through prompts / synthesizer contract.

### Contributing / dev

```bash
python -m pytest
```

Fast, deterministic tests (no live `gemini` subprocess).

## Session reuse (optional)

GemmaPie can **reuse past sessions** to save work. Matching uses **similar wording**, not true “understanding,” and **old answers can be wrong**. Defaults favor **inject** (prior text as background; **full debate still runs**). Copying an old answer with **no** API calls is **expert-only** and off by default.

**Read this first (non-technical):** [`docs/SESSION_REUSE_USER_GUIDE.md`](docs/SESSION_REUSE_USER_GUIDE.md)

## Configuration

- **Timeouts / per-agent models / context budget / API pacing:** [`config.yaml`](config.yaml) (`rate_limit` reduces burst traffic to the CLI)
- **Adaptive tier routing (T0/T1/T2):** [`config.yaml`](config.yaml) `adaptive` and [`docs/ADAPTIVE_TIERS.md`](docs/ADAPTIVE_TIERS.md)
- **Session reuse gates (word overlap, max age, zero-call opt-in):** [`config.yaml`](config.yaml) `session_reuse` and the user guide above
- **Role instructions:** [`prompts/`](prompts/)
- **Sampling intent vs CLI reality:** [`agent_configs.md`](agent_configs.md)

## Troubleshooting (Windows / Gemini CLI)

| Symptom | What to try |
|---------|----------------|
| `` `gemini` exited with code 130 `` | Often **interrupt** (Ctrl+C / SIGINT) or the CLI aborting mid-run. Avoid sending a second interrupt while a step is running. With the updated runner, Ctrl+C should **kill the child `gemini` process** instead of hanging in `communicate`. |
| `Ripgrep is not available` in stderr | Install [Ripgrep](https://github.com/BurntSushi/ripgrep/releases) so `rg` is on `PATH`, or follow [Gemini CLI](https://github.com/google-gemini/gemini-cli) issues for Windows ripgrep detection. The CLI may warn or degrade search without it. |
| Rich Live + extra console quirks | Each `gemini` call uses `CREATE_NO_WINDOW` on Windows and **`cwd` = the session folder** (not the whole repo) so the CLI indexes a small workspace. |
| `` `gemini` exited with code 41 `` + “must specify … API_KEY” | Set **`GEMINI_API_KEY`** or **`GOOGLE_API_KEY`** (see [`.env.example`](.env.example)) or run the CLI [authentication](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html) flow. Until this works, `report.md` will show every agent as failed and the **FINAL ANSWER** block will say no integrated answer was produced. |
| Quota / 429 / “rate limit” / `TerminalQuotaError` | The run uses [`config.yaml`](config.yaml) **`rate_limit`** (retries + backoff). Raise limits in Google AI Studio / billing, reduce agents (e.g. adaptive T1), use lighter models, or increase `min_interval_s` / lower concurrency. |
| `ModelNotFoundError` / “Requested entity was not found” (404) | Your CLI/API build does not expose that `-m` id. Run **`gemini models list`**, copy an exact **`gemini-*`** string into **`model`** and every **`models:`** line in [`config.yaml`](config.yaml) (and `adaptive.*` if used). Prefer **`gemini-2.5-flash`** or **`gemini-2.5-pro`** per current CLI docs. |

## Anchor demo (video)

See [`DEMO_CHECKLIST.md`](DEMO_CHECKLIST.md) for a short checklist when recording the hackathon demo.

## License

MIT — see [`LICENSE`](LICENSE).
