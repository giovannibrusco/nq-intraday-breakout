# AGENTS.md

Brief instructions for AI agents working in this repo.

## Repo role

This is **nq-intraday-breakout-v2**, a passive data/artifact repo:

- NQ intraday breakout strategy code (`src/nqbreakout/`)
- Fixture generation scripts (`scripts/`)
- **Canonical prop firm rules** (`docs/firms/*.yaml`) — source-cited YAML files that are mirrored into `prop-evaluator/configs/firms/`

Active development happens in the companion repo `~/Desktop/prop-evaluator/`. Sessions here are occasional (e.g., regenerate a fixture, update a canonical firm YAML).

## Canonical policy lives elsewhere

The full policy file (lessons learned, source provenance policy, conventions) is in:

```
~/Desktop/prop-evaluator/AGENTS.md
```

Read it if you are going to make any non-trivial change. The policies that apply here are a subset of those.

## The one policy that ALWAYS applies in this repo

If you modify any `docs/firms/*.yaml`:

> **Every numeric field must have an inline `# Fonte: <URL>` comment pointing to the official firm documentation.** No placeholders. No "plausible" values without source. If a value is derived, document the derivation explicitly. Origin of this rule: a placeholder `min_trading_days: 7` once invalidated P(pass) results in `prop-evaluator` because it had no source comment to challenge.

If you cannot find an official source for a value, leave it `null` or raise an error — never invent.

## Cross-repo coordination

Changes to `docs/firms/<firm>.yaml` here must be reflected in `prop-evaluator/configs/firms/<firm>.yaml` (manual sync for now). Note this in the commit message so it's tracked.

## Project state

For the current state of the broader project (validated numbers, planned next steps, lessons learned in context), read `docs/state-of-project.md`.
