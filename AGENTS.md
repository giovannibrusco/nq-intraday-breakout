# AGENTS.md

Instructions for AI agents (Claude Code, Codex CLI, etc.) working in this repo.

## Repo role

This repo is **nq-intraday-breakout-v2**: backtest engine, fixture generation, and canonical prop firm rules documentation.

It is paired with `~/Desktop/prop-evaluator/`, which is the simulation engine that consumes trade-level fixtures produced here and runs Monte Carlo / Markov regime simulations against prop firm rules.

Division of responsibility:

| Concern | Where it lives |
|---|---|
| NQ backtest strategy code | `src/nqbreakout/` (here) |
| Fixture generation scripts | `scripts/` (here) |
| Canonical prop firm rules (YAML, source-cited) | `docs/firms/` (here) |
| Simulation engine, RuleEngine, regime detector | `prop-evaluator` (separate repo) |
| Firm config consumed by simulator | `prop-evaluator/configs/firms/` (mirrored from `docs/firms/` here) |

When you change rules in `docs/firms/<firm>.yaml`, the corresponding `prop-evaluator/configs/firms/<firm>.yaml` must be updated too. They are kept in sync manually for now (see `docs/state-of-project.md`).

## Bootstrap before any task

Read these files at session start, in this order:

1. `docs/state-of-project.md` — current state, validated numbers, planned next steps
2. This file (`AGENTS.md`) — policies and lessons learned
3. Files relevant to the specific task (e.g., `docs/firms/<firm>.yaml` if modifying firm rules)

## Lessons learned (mandatory reading)

These are codified policies from real bugs encountered during development. Violating them re-introduces bugs we've already paid for. Apply them without exception.

### Lesson 1 — Source provenance policy

Every file in `docs/firms/` represents the official rules of a prop firm. The numbers in these files determine downstream simulation results (P(pass), expected payouts, etc.). A single wrong number invalidates every analysis built on top.

**Acceptance criterion (NO EXCEPTIONS)**:

Every numeric field in a firm config YAML MUST have an inline comment `# Fonte: <URL>` or `# Source: <URL>` pointing to the official firm documentation it was extracted from. If the value is derived (e.g., `safety_net = starting + drawdown + buffer`), the comment must state the derivation:

```yaml
# Derivato: starting_balance + drawdown + buffer (Fonte: <URL>)
```

**Origin**: In v0.2 of `prop-evaluator`, the field `min_trading_days: 7` was an LLM placeholder with no source. It silently invalidated P(pass) numbers for days. The bug was invisible because the value had no source comment to challenge. NEVER repeat this pattern.

When creating or modifying a firm config:
1. Extract values from the **official firm documentation** (Claude in Chrome for web extraction, or manual consultation — NOT from model memory)
2. For every number, add `# Fonte: <URL>`
3. Commit/PR description must list modified fields with source links
4. **Never use "plausible" values without a source**: a `NotImplementedError` or `null` is better than a placeholder that looks real

### Lesson 2 — Predictions vs measurements

When suggesting a quantitative expectation ("P(pass) will drop by 15-30 pp"), this is a **hypothesis** that guides measurement priorities, NOT a finding. Always validate empirically before treating as truth.

**Origin**: On 2026-05-11, the hypothesis "NQ Apex 25K Eval P(pass) will drop by 15-30 pp when constraining to 30-day horizon" was false. The measurement showed NQ is insensitive to horizon (P(timeout)=0 at 30 days already, paths terminate fast either pass or violate). Hypotheses are useful to prioritize what to measure, but they are not the answer.

### Lesson 3 — Block bootstrap sparsity bias

If a strategy has trades on fewer than 60% of business days (sparse activity), block bootstrap of daily PnL inflates activity by `1 / active_day_rate` and biases P(pass). The bias direction depends on the binding constraint:

- **DD binding** (e.g., NQ Phase E): more activity → more violations → P(pass) block **lower** than truth
- **Profit-target binding** (e.g., MNQ on Apex 25K): more activity → more profit → P(pass) block **higher** than truth

Use **Markov regime bootstrap as primary** for sparse strategies. Block bootstrap is reference only, with automatic warning if `|P(block) - P(Markov)| > 10pp`. Long-term fix (not yet implemented): block bootstrap should sample calendar days including zero-trade days for parity with Markov.

## Conventions

- **Repo paths**: always absolute when calling tools, e.g., `/Users/giovannibrusco/Desktop/nq-intraday-breakout/scripts/...`
- **YAML in `docs/firms/`**: must follow the source provenance policy above. Values monetary as integers (`25000`) or strings with `$` (`"$25,000"`), consistent within file.
- **Python**: type hints throughout, ruff/mypy strict, pytest for tests
- **Git**: commit messages explain "why" not just "what"; large refactors in separate commits from policy/doc updates

## Cross-repo coordination

When working in this repo on something that affects `prop-evaluator`:
- Update `docs/state-of-project.md` with a note
- Document what needs to change in `prop-evaluator` in the commit message
- Do NOT modify `prop-evaluator/` files from this repo's session

## Companion symlink

`CLAUDE.md` in this repo is a symlink to `AGENTS.md`. They are the same file. Claude Code reads `CLAUDE.md` automatically; Codex CLI reads `AGENTS.md` automatically. The symlink ensures both AIs see the same policies.
