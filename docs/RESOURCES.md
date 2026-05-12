# Resources — Gemma 4 Distributed Cognition

Judge- and developer-facing index. Paths are relative to the repository root.

## Internal documentation

| Document | Description |
|----------|-------------|
| [../implementation_plan.md](../implementation_plan.md) | Phased roadmap, transparency goals, hackathon deliverables |
| [../agent_configs.md](../agent_configs.md) | Model ids (Gemma 3/4), L/M/S tier map, sampling intent, thinking contract |
| [../plan.md](../plan.md) | Technical spec: goals, non-goals, architecture narrative |
| [../README.md](../README.md) | Install, run commands, session artifacts |
| [../DEMO_CHECKLIST.md](../DEMO_CHECKLIST.md) | Short demo recording checklist |

## Runtime configuration

| File | Description |
|------|-------------|
| [../config.yaml](../config.yaml) | `model`, per-role `models:`, timeouts, `thinking.enabled`, pipeline flags |

## External references

### Gemini CLI

- [Headless mode](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html) — `-p`, `--output-format json`, `-m`
- [Authentication](https://google-gemini.github.io/gemini-cli/docs/get-started/authentication.html)
- [Configuration](https://google-gemini.github.io/gemini-cli/docs/get-started/configuration.html)

### Research papers (implementation_plan citations)

- Recursive multi-agent systems: [arxiv:2604.25917](https://arxiv.org/pdf/2604.25917)
- Diversity collapse in multi-agent systems: [arxiv:2604.18005](https://arxiv.org/pdf/2604.18005)
- OneManCompany: [arxiv:2604.22446](https://arxiv.org/pdf/2604.22446)
- HALO (reference implementation): [github.com/context-labs/halo](https://github.com/context-labs/halo)

## Model verification

Use the CLI or API model list for your channel; then align ids in `agent_configs.md` and `config.yaml`. See README “Model verification” for suggested smoke steps.
