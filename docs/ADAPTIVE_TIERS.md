# Adaptive tier routing

This describes behavior only (no performance claims).

## Tiers

| Tier | Meaning |
|------|---------|
| **T0** | Deterministic path for a single class of questions: bounded positive integer addition `a + b` detected by a strict heuristic (regex + integer checks, no `eval`). No Gemini calls for researcher/skeptic/contrarian/reviewer/synthesizer roles; outputs are assembled locally and written like a normal session. |
| **T1** | Researcher and Synthesizer only, using `adaptive.models_light` when set (otherwise global `model` / `models:`). Skeptic, Contrarian, Reviewer, and round-two agents are not invoked; their slots in the session are explicit placeholders. |
| **T2** | Full pipeline: same as when adaptive routing is off — sequential (or parallel first turn if configured), disagreement handling, optional round two, Synthesizer at the end. |

## Configuration (`config.yaml` → `adaptive`)

- **`enabled`** (bool): Master switch. If false, no classification runs and the pipeline behaves as before.
- **`router`**: `off` \| `heuristic` \| `heuristic_then_slm`
  - **`off`**: No routing (same as disabled for classification purposes when combined with `enabled: false` logic — routing runs only when `enabled` is true and router is not `off`).
  - **`heuristic`**: T0 when the heuristic matches; if the heuristic does not match, tier is **T2** (no SLM).
  - **`heuristic_then_slm`**: T0 when the heuristic matches; if not, one small Gemini call (`classify_slm`) returns **T1** or **T2** from JSON; failures or bad JSON map to **T2** with reason `router_parse_failed` or `router_call_failed:…`.
- **`router_model`**, **`router_timeout`**: Used only for `heuristic_then_slm` when the SLM router runs.
- **`models_light`**: At minimum `researcher` and `synthesizer` keys; used only on the **T1** path for those two calls.
- **`fallback_to_full`**: On **T1**, if the Synthesizer step fails, returns unusable text, or (when `require_claims_for_t1_fallback` is true) the `## Claims` JSON block is missing or unparsable, the run discards the partial T1 state and executes the **full T2** pipeline once with `tier_locked_t2` so adaptive logic does not re-enter.
- **`max_trivial_add_digits`**: Maximum decimal digits per operand for **T0** heuristic matching.
- **`require_claims_for_t1_fallback`**: When true, missing or empty parsed claims from the Synthesizer output triggers **T1** → **T2** fallback if `fallback_to_full` is true.

## CLI

`python orchestrate.py --adaptive off|heuristic|heuristic_then_slm "…"`

- **`off`**: Forces router off for this run (no adaptive classification).
- **`heuristic`** / **`heuristic_then_slm`**: Sets the effective router for this run. Classification still requires **`adaptive.enabled: true`** in config.

## Session artifacts

- **`adaptive_tier.json`**: Written when a tier other than “plain” (non-adaptive) run is selected; includes `tier` and routing metadata.
- **`disagreements.json`**: Always written for T0/T1/T2 paths that complete the pipeline module; content follows the same schema as the full run where applicable.

## Session reuse

If **`short_circuit`** session reuse applies, adaptive classification is **skipped** entirely (zero Gemini calls, including the router).
