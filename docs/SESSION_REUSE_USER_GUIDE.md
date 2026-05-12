# Session reuse — plain-language guide (GemmaPie)

This page is for **everyone**, not only developers. Technical details live in code comments and `config.yaml`.

---

## What you are asking GemmaPie to do

Sometimes you ask a question that is **almost the same** as one you asked before. GemmaPie can **look in your past sessions** (saved under `sessions/` on your computer) and reuse that work in two different ways.

---

## Requirement 1 — “Similar” means similar **words**, not similar **meaning**

GemmaPie compares questions using **spelling and word overlap**, not true understanding.

- Two questions can use the **same words** but mean **different things** (GemmaPie may still treat them as a match).
- Two questions can mean the **same thing** but use **different words** (GemmaPie may **not** match them).

**What we do to reduce mistakes**

- We require both **character similarity** and **important-word overlap** (see `min_word_overlap` in `config.yaml`).
- We **ignore past sessions** older than **`max_reuse_session_age_days`** (default: 14 days), so old answers are not pulled in automatically.

True “same meaning, different words” matching would need **semantic search** (usually extra API calls and setup). GemmaPie does **not** do that by default so the project stays simple and predictable.

---

## Requirement 2 — Stale answers (out-of-date information)

An old answer can be **wrong** today (laws change, prices change, science updates, your situation changed).

### Safe default: **Inject** (recommended)

- GemmaPie may **paste a short summary** of an old answer into the **first** agent only (the Researcher), as **background**.
- **All agents still run** afterward. The pipeline produces a **new** debate and a **new** final answer.
- This **does not** remove the risk of bad matches, but it **does** force a fresh pass through the system.

### Expert-only: **Zero-call shortcut** (not recommended for most people)

- If turned on, GemmaPie can **skip running the models** and **copy** an old final answer. That saves time and API usage but can **serve a stale or mismatched answer**.
- This is **off by default**. You must explicitly set **`allow_zero_call_reuse: true`** in `config.yaml` **or** pass **`--allow-zero-call-reuse`** on the command line **and** use **`short_circuit`** mode.

If you are not comfortable with that tradeoff, **leave zero-call reuse off** and use **inject** only.

---

## Requirement 3 — How to turn reuse on or off

| Goal | What to do |
|------|------------|
| **Off** | In `config.yaml`, set `session_reuse.enabled: false` and do not set `SESSION_REUSE=1`. Do not pass `--reuse-similar`. |
| **On (safe)** | Set `session_reuse.enabled: true` and keep **`mode: inject`**. Adjust `similarity_threshold` and `min_word_overlap` if you get too many or too few matches. |
| **On (expert, zero calls)** | Set `allow_zero_call_reuse: true`, set `mode: short_circuit`, and read Requirement 2 again. |

Environment variables (optional): `SESSION_REUSE=1` turns reuse on like the config flag. There is **no** environment variable that enables zero-call reuse without the config or CLI flag — that is intentional.

---

## Summary table

| Mode | Calls the AI? | Stale-answer risk | Who it is for |
|------|----------------|-------------------|---------------|
| **inject** | Yes | Lower (full new run) | Most people |
| **short_circuit** | No | **Higher** | Experts who accept stale risk |
| **off** | Yes | No reuse | Default if you do not enable reuse |

---

## Still unsure?

Use **inject** with defaults, or turn reuse **off**. When in doubt, prefer a **new** full run over a **copied** old answer.
