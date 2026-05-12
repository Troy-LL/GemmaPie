# GemmaPie · Gemma 4 Distributed Cognition System

**Repository:** [github.com/Troy-LL/GemmaPie](https://github.com/Troy-LL/GemmaPie)

Multi-agent **peer research** orchestrated in Python around the **Google Gemini CLI** in headless JSON mode. Five roles (Researcher, Skeptic, Contrarian, Reviewer, Synthesizer) run sequentially—optionally with **parallel** Researcher+Skeptic on the first turn—share a session scratchpad, log **confidence disagreement**, optionally run a **one-step recursive** Researcher/Skeptic refinement, and emit **transparent session artifacts** (`report.md`, `transcript.md`, `audit.json`).

**Tracks (hackathon framing):**

- **Safety & Trust:** disagreement and per-agent confidence are surfaced, not smoothed away; session logs support auditability.
- **Future of Education:** `transcript.md` is readable as a debate trace for learners.

## Prerequisites

1. **Python 3.10+**
2. **Google Gemini CLI** installed and on `PATH` as `gemini`, authenticated per [Authentication](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html).
3. Model ids your account can run. Set **`model`** (global default) and optional **`models:`** per role in [`config.yaml`](config.yaml). Defaults target **heterogeneous Gemma 4** tiers (see tables in [`agent_configs.md`](agent_configs.md)). Use **Gemma 3** or a single shared `model` if quota or VRAM is tight.

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

**Plain logs** (no Rich live panel):

```bash
python orchestrate.py --no-dashboard "Your question here"
```

**Thinking traces** in reports and `*_thinking.txt` files:

```bash
python orchestrate.py --show-thinking "Is nuclear energy safe?"
```

## Session outputs

Each run creates `sessions/session_YYYYMMDD_HHMMSS/` containing:

| File | Purpose |
|------|---------|
| `question.txt` | User question |
| `scratchpad.md` | Chronological agent outputs |
| `shared_facts.md` | Lines extracted from Researcher outputs prefixed with `SHARED_FACT:` |
| `disagreements.json` | Confidence spread analysis + structured entries |
| `models_used.json` | Resolved `-m` model id per role (including round-2 inheritance) |
| `*_raw.json` | Raw Gemini CLI JSON (per step) |
| `report.md` | Human-readable transparency report + epistemic status |
| `transcript.md` | Debate-style ordering of outputs |
| `audit.json` | Parsed `## Claims` JSON from the Synthesizer (when present); `meta.models_used` |
| `*_thinking.txt` | Per-agent reasoning trace when thinking mode is on |

Full link index: [`docs/RESOURCES.md`](docs/RESOURCES.md).

## Configuration

- **Timeouts / per-agent models / context budget:** [`config.yaml`](config.yaml)
- **Role instructions:** [`prompts/`](prompts/)
- **Sampling intent vs CLI reality:** [`agent_configs.md`](agent_configs.md)

## Troubleshooting (Windows / Gemini CLI)

| Symptom | What to try |
|---------|----------------|
| `` `gemini` exited with code 130 `` | Often **interrupt** (Ctrl+C / SIGINT) or the CLI aborting mid-run. Avoid sending a second interrupt while a step is running. With the updated runner, Ctrl+C should **kill the child `gemini` process** instead of hanging in `communicate`. |
| `Ripgrep is not available` in stderr | Install [Ripgrep](https://github.com/BurntSushi/ripgrep/releases) so `rg` is on `PATH`, or follow [Gemini CLI](https://github.com/google-gemini/gemini-cli) issues for Windows ripgrep detection. The CLI may warn or degrade search without it. |
| Rich Live + extra console quirks | Each `gemini` call uses `CREATE_NO_WINDOW` on Windows and **`cwd` = the session folder** (not the whole repo) so the CLI indexes a small workspace. |

## Anchor demo (video)

See [`DEMO_CHECKLIST.md`](DEMO_CHECKLIST.md) for a short checklist when recording the hackathon demo.

## License

MIT — see [`LICENSE`](LICENSE).
