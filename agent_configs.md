# Agent configurations — Gemma + Gemini model catalog

Phases, demo checklist, and hackathon narrative: [implementation_plan.md](implementation_plan.md). Resource index: [docs/RESOURCES.md](docs/RESOURCES.md).

This file is the **operational catalog**: model ids, tier map, prompt-level sampling intent, **thinking trace contract**, and CLI caveats.

This project **tunes sampling behavior in the prompt** (role instructions). The **Google Gemini CLI** (as of May 2026) exposes model selection (`-m` / `--model`) and headless prompts (`-p` / `--prompt`), plus `--output-format json`. It does **not** expose per-invocation `temperature` / `top_p` flags in `gemini --help`.

> **Verify ids** against your CLI / API release (`gemini models list` or current Google docs). Naming can differ by channel; preview ids may appear, rename, or disappear between CLI releases.

## Google Gemini — 3.x series (latest generation)

Flagship reasoning and agentic workflows; preview names change over time—confirm with `gemini models list`.

| Model id | Summary |
|----------|---------|
| `gemini-3.1-pro-preview` | **Flagship:** strongest reasoning, advanced logic, deep agentic coding when you want the CLI to operate autonomously on hard tasks. |
| `gemini-3-flash-preview` | **Sweet spot:** near-Pro capability with lower latency and cost; good default when 3.x is available and you want speed + intelligence. |
| `gemini-3.1-flash-lite` | **Stable** ultra-fast, budget-friendly workhorse for massive repetitive operations. |
| `gemini-3.1-flash-lite-preview` | Older preview name for the same “lite” tier; use whichever your CLI lists. |

## Google Gemini — 2.5 series (stable generation)

Widely available defaults; many installs use **Flash** on first use.

| Model id | Summary |
|----------|---------|
| `gemini-2.5-pro` | Prior flagship; deep reasoning and very large context windows for big codebases. |
| `gemini-2.5-flash` | **Balanced default:** speed, context, and quality for day-to-day work. |
| `gemini-2.5-flash-lite` | Fastest, most budget-friendly multimodal option in the 2.5 family for quick terminal queries. |

**Older ids** (e.g. `gemini-2.0-flash`) may return **404** on newer API builds—prefer **2.5** or **3.x** strings from `gemini models list`.

---

## Gemma 4 series (released April 2026)

Google’s newest generation mixes edge-friendly and server-grade **multimodal** checkpoints (ids below are what you pass to `-m` when the CLI/API exposes them).

| Model id | Summary |
|----------|---------|
| `gemma-4-e2b-it` | ~2.3B effective params; INT4-friendly (~3.2GB VRAM); strong fit for **Tier S** / edge / fast passes. |
| `gemma-4-e4b-it` | ~4.5B effective; slightly more capable **mobile / low-resource** deployments. |
| `gemma-4-26b-a4b-it` | 26B **MoE**; large static weight footprint but ~**4B active** per generation — good **Tier M** speed/quality tradeoff. |
| `gemma-4-31b-it` | Dense **31B flagship**; heavy VRAM — best for **Tier L** synthesis or review when hardware allows. |

## Gemma 3 series (2025)

Still fully supported via the API; useful **fallbacks** if a Gemma 4 tier is unavailable or over quota.

| Model id | Summary |
|----------|---------|
| `gemma-3-1b-it` | Ultra-light 1B text. |
| `gemma-3-4b-it` | Balanced ~4B. |
| `gemma-3-12b-it` | Mid ~12B. |
| `gemma-3-27b-it` | Dense ~27B flagship (prior gen). |

## Specialty models

| Model id | When to use |
|----------|-------------|
| `codegemma-7b-it` | Code-heavy CLI tasks (generation, refactor hints). |
| `paligemma-2-10b-it` | Vision-language; multimodal prompts with **images** in the loop. |

---

## Heterogeneous default map — **shipped `config.yaml` (Gemini API)**

| Tier | Intent | Default id in `config.yaml` | Roles |
|------|--------|-------------------------------|--------|
| **L** | Heaviest reasoning / user-facing merge | `gemini-2.5-pro` | `reviewer`, `synthesizer` |
| **M** | Long analysis + structured dissent | `gemini-2.5-flash` | `researcher`, `contrarian` |
| **S** | Fast skeptical / framing pass | `gemini-2.5-flash-lite` | `skeptic` |

**Round 2:** `researcher_round2` inherits `models.researcher`; `skeptic_round2` inherits `models.skeptic` unless you add explicit keys under `models:`.

**Adaptive T1** (`models_light`): uses lighter researcher + synthesizer ids in `config.yaml` when adaptive routing selects T1.

**Quota / 404:** If `gemini-2.5-pro` or `gemini-2.5-flash-lite` is not listed for your account, set those roles to `gemini-2.5-flash` (single id for all roles is the most compatible).

**Global fallback:** `model:` in `config.yaml` is used when `models.<agent>` is missing.

### Optional Gemma 4 map (when your key lists Gemma ids)

| Tier | Intent | Example id | Roles |
|------|--------|------------|--------|
| **L** | Heaviest reasoning / merge | `gemma-4-31b-it` | `reviewer`, `synthesizer` |
| **M** | Long analysis + dissent | `gemma-4-26b-a4b-it` | `researcher`, `contrarian` |
| **S** | Fast skeptic pass | `gemma-4-e4b-it` | `skeptic` |

### Optional Gemini 3.x map (when previews are listed)

| Tier | Intent | Example id | Roles |
|------|--------|------------|--------|
| **L** | Flagship merge / review | `gemini-3.1-pro-preview` | `reviewer`, `synthesizer` |
| **M** | Main analysis + contrarian | `gemini-3-flash-preview` | `researcher`, `contrarian` |
| **S** | Fast skeptic | `gemini-3.1-flash-lite` or `…-preview` | `skeptic` |

---

## VRAM / quota cost ladder (practical)

| If this fails… | Try (in order) |
|----------------|----------------|
| Any `gemini-*` **404** / “entity not found” | `gemini models list` → copy exact strings; often **`gemini-2.5-flash`** is the safest single id for every role. |
| `gemini-2.5-pro` quota / cost | `gemini-2.5-flash` for reviewer + synthesizer |
| `gemini-2.5-flash-lite` not listed | `gemini-2.5-flash` for skeptic only |
| Want latest gen when listed | Tier **M/S** to `gemini-3-flash-preview`, **L** to `gemini-3.1-pro-preview` (see tables above) |
| `gemma-4-31b-it` (Tier L) OOM / quota | `gemma-4-26b-a4b-it` for reviewer + synthesizer |
| Still too heavy | `gemma-3-27b-it` for Tier L roles |
| `gemma-4-e4b-it` (Tier S) | `gemma-4-e2b-it` or `gemma-3-4b-it` for skeptic only |
| All Gemma 4 ids rejected | Use **Gemini** tier map above or a single `gemini-2.5-flash` on `model:` and every `models:` line |

---

## Thinking trace contract (matches `src/parsing.py`)

1. **Preferred:** wrap private step-by-step reasoning in `<thinking>...</thinking>` at the **start** of the model reply (see `prompts/*.txt`).
2. **Alternate:** `## Scratch reasoning` section ending before the next `## `, `---`, or `Confidence:` line (see parser).
3. **Native JSON:** if the CLI returns a string field such as `thoughts` / `reasoning` in headless JSON, it is prepended (labeled `[from CLI JSON]`) in `*_thinking.txt` when thinking mode is on.
4. **Inter-agent context:** only text **outside** `<thinking>` is passed to downstream agents (strip applied in `src/pipeline.py`).

---

## Intended sampling profiles (documentary / prompt-embedded)

| Agent | Role | Intended temperature | Intended top-p | Persona |
|-------|------|---------------------|----------------|---------|
| Researcher | Evidence-first baseline | 0.3 | 0.90 | Thorough, neutral, evidence-first |
| Skeptic | Challenge assumptions | 0.8 | 0.95 | Challenging, probing, finds weaknesses |
| Contrarian | Surface minority views | 1.0 | 1.00 | Opposes easy consensus, stresses alternatives |
| Reviewer | Structured evaluation | 0.2 | 0.85 | Evaluative, structured, rates confidence |
| Synthesizer | Integrative answer | 0.4 | 0.90 | Integrative, balanced, final output |

**CLI reality:** `gemini_runner.py` invokes `gemini -p ... -m <model> --output-format json` only. To approximate lower variance for Reviewer / Researcher and higher for Contrarian / Skeptic, prompts explicitly ask for tighter vs more exploratory reasoning **without** claiming API-level temperature control.

## Required response footer (all agents)

Every agent must end with:

```text
Confidence: X/10
Key uncertainty: ...
```

## Anti-sycophancy

Skeptic and Contrarian prompts include explicit instructions **not** to rubber-stamp prior agents.
