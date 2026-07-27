# Simulation bias audit

This repository has produced **two** result sets from the same market data.
They differ because the simulator's conventions changed, not because the
strategy changed. This document lists every difference, says which are
genuine defects and which are modelling choices, and attributes the change
in the headline numbers.

| | Legacy (published on `main`) | Corrected |
|---|---:|---:|
| Net PnL, 1% risk | $3,287,392 | $2,800,810 |
| Trades | 1,728 | 1,725 |
| PnL / contract | $191.33 | $176.94 |
| Sharpe | 0.95 | 1.02 |
| Volatility | 11.25% | 14.12% |
| Max drawdown | 15.06% | 16.82% |
| QQQ benchmark Sharpe | 0.64 | 0.77 |

Both sets are internally consistent: every figure in each README matches
the embedded outputs of the notebook that produced it.

**Verified end to end on the real 10-year feed** (693,117 five-minute
bars): `tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq
Data/QQQ_daily.csv` reproduces both endpoints exactly —
1,728 trades / $3,287,392.20 / Sharpe 0.9480 (legacy) and
1,725 trades / $2,800,810.00 / Sharpe 1.0165→1.02 (corrected) — to the
cent and to two Sharpe decimals. This is not an estimate; every number in
the tables below (except the "not a bias" section, which was always
data-derived) now comes from that run.

---

## The headline result: what actually moved the number

Net PnL fell 14.8% ($486,582). Almost none of that is bias correction —
confirmed on the real feed, not inferred from two endpoints:

| Configuration | Net PnL | Trades | Sharpe |
|---|---:|---:|---:|
| Close-fill entries, legacy conventions (`main`) | $3,287,392.20 | 1,728 | 0.95 |
| Close-fill entries, **all corrections applied** | $3,323,800.00 | 1,736 | — |
| Stop-fill entries, all corrections applied | $2,800,810.00 | 1,725 | 1.02 |

Reading down the table:

- **All eleven bias corrections together were worth +$36,407.80 (+1.1%) on
  PnL**, exact, read directly off the attribution run
  (`3,323,800.00 − 3,287,392.20`). They netted small because they point in
  both directions, not because each one is individually small — see the
  per-step table below, where the biggest single steps move six figures.
- **The entry convention was worth exactly −$522,990.00 (−15.7%)**,
  likewise read directly off the run. Switching from filling at the
  breakout bar's close to filling at the breakout level is a *modelling
  choice*, not a defect fix, and it accounts for more than 100% of the
  drop in the headline number.

This matters for how the change should be described. "We found several
biases and the number went down" is not what the data shows: the
corrections roughly cancel, and the entry-convention choice does the work.

### The full step-by-step chain, on real data

Each row adds one correction on top of the row above (cumulative, not
isolated — see the caveat below the table). `Δ net_pnl` and `Δ sharpe` are
the marginal effect of that one step.

| Step | Trades | Δ trades | Net PnL | Δ net PnL | Sharpe | Δ Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| LEGACY (all original conventions) | 1,728 | — | 3,287,392.20 | — | 0.9480 | — |
| cost model ($9.20 → $20.00 RT) | 1,728 | 0 | 3,019,805.00 | −267,587.20 | 0.9104 | −0.0376 |
| gap-aware fills | 1,728 | 0 | 3,247,105.00 | +227,300.00 | 0.8732 | −0.0372 |
| scan overnight bars | 1,819 | +91 | 2,733,820.00 | −513,285.00 | 0.8552 | −0.0180 |
| exclusive window end | 1,852 | +33 | 2,660,005.00 | −73,815.00 | 0.8361 | −0.0191 |
| block last-bar entry | 1,842 | −10 | 2,582,100.00 | −77,905.00 | 0.8299 | −0.0063 |
| max-hold exit at session close | 1,768 | −74 | 2,723,915.00 | +141,815.00 | 0.8418 | +0.0120 |
| CME trading-day mapping | 1,736 | −32 | 3,323,800.00 | +599,885.00 | 0.9268 | +0.0850 |
| min-1-contract / prev-session floor | 1,736 | 0 | 3,323,800.00 | 0.00 | 0.9268 | 0.0000 |
| daily mark-to-market | 1,736 | 0 | 3,323,800.00 | 0.00 | 0.9193 | −0.0075 |
| entry_mode: close → stop | 1,725 | −11 | 2,800,810.00 | −522,990.00 | 0.8432 | −0.0761 |
| drop calendar padding | 1,725 | 0 | 2,800,810.00 | 0.00 | 1.0165 | +0.1733 |

**Caveat on reading individual rows.** The steps are applied cumulatively,
in this order, so a row's Δ is the marginal effect *given every correction
above it already applied* — not that bias's isolated effect against the
untouched legacy baseline. The engine is also path-dependent (position
sizing depends on the running account value, so one changed trade shifts
the size, and sometimes the existence, of every trade after it). Two
consequences worth naming rather than glossing over:

- **`block last-bar entry` moves the wrong way for its own stated
  mechanism.** Removing 10 trades that were guaranteed near-breakeven
  losers (a bad fill with no time to move) should free roughly
  10 × $20 ≈ $200. It actually cost $77,905. That gap is the cascading
  effect: removing those 10 entries changed the account-value path,
  which changed sizing and downstream trade generation for the rest of
  the sample. The defect claim (#7 below) still holds; the *dollar
  figure* attached to it is a chain effect, not the isolated cost of
  those 10 fills.
- **`CME trading-day mapping` is not the minor, derivative item this
  document originally called it.** It moves $599,885 — the second-largest
  single step after gap-aware fills, larger than the entire net effect of
  every correction combined. See the correction to item #9 below.

### 1. Equity curve padded to calendar days — CONFIRMED, quantified exactly

**Where:** original notebook, equity construction —
`pd.date_range(start, end, freq='D')` then `.ffill()`, with
`compute_metrics(..., freq=252)`.

**What:** the equity series was spread over all ~365 calendar days per
year, inserting ~113 flat days, while still being annualised with 252.

**Direction:** legacy **understates** Sharpe and volatility by a factor of
`sqrt(252/365) = 0.8309`.

**Magnitude — verified on the real data, not estimated.** QQQ is a perfect
control here: the benchmark's own price data is identical in both runs, so
any change in its metrics is purely the date grid.

| | Legacy | Corrected | Ratio | `sqrt(365/252)` |
|---|---:|---:|---:|---:|
| QQQ Sharpe | 0.6375 | 0.7683 | 1.2052 | 1.2036 |
| QQQ volatility | 18.36% | 22.11% | 1.2043 | 1.2036 |

Both match the closed form to within 0.13%. The entire benchmark
improvement is this one arithmetic artefact and nothing else.

**Confidence: certain.** This is the single largest driver of the Sharpe
change, and it makes the strategy look *better*, not worse.

### 2. Slippage charged on one side, not two — CONFIRMED, exact

**Where:** original notebook —
`slippage_per_contract = SLIPPAGE_PER_SIDE_TICKS * SLIPPAGE_PER_TICK_VALUE`.
The variable is named "per side" and the README documented "1.5 ticks of
slippage per side ... debited on every round turn", but it is never
doubled.

**Direction:** legacy **overstates** PnL.

**Magnitude:** exactly $7.50/contract/round-turn of missing slippage.

**Confidence: certain** — the code contradicts its own documentation.

### 3. Commission $0.85/leg → $2.50/side — a CHANGED ASSUMPTION, not a fix

**Where:** `FEE_PER_LEG = 0.85` ("Interactive Brokers fee per leg").

$0.85 is IB's *broker* commission alone; all-in cost including exchange,
clearing and NFA fees is around $2.30–2.60 per side for NQ. So the new
figure is more realistic — but this is a judgement call about what to
assume, not a bug that was fixed. It should be presented as such.

**Combined effect of #2 and #3:** round-turn cost $9.20 → $20.00, a flat
**$10.80 per contract per round turn**. Verified analytically and pinned
by a test (`test_cost_delta_per_contract_is_exactly_10_80`). Against the
published run that is roughly 1,728 × 9.37 × $10.80 ≈ **$175k**, about 36%
of the total PnL drop.

### 4. Stops and targets filled at the level through gaps — CONFIRMED, direction is NOT one-sided (correction to this document)

**Where:** original exit logic — `if row.low <= stop: exit_price = stop`,
with no check on `row.open`.

**What:** a bar that *opened* beyond a stop or target level still filled
at the level, ignoring the open. A real resting order fills at the open
when the market gaps through it.

**This document previously said "legacy overstates PnL, unambiguously."
That claim does not survive contact with the real data and is wrong as
stated.** The bug is symmetric, not one-sided:

- On a **stop**, filling at the level instead of the (worse) open makes
  the legacy fill *better* for the trader than reality → legacy
  overstates PnL.
- On a **target**, filling at the level instead of the (better) open
  makes the legacy fill *worse* than reality → legacy **understates**
  PnL.

Which effect dominates is an empirical question about how often the
market gaps through a stop versus through a target in this sample — not
something derivable from the code. On the real 10-year feed the target
side won: turning gap-aware fills on **raised** net PnL by $227,300 (row
2 of the step table). The synthetic-bars run in this repository's test
suite showed the opposite sign (−$287k) purely because that generator is
an unconditioned random walk with no drift asymmetry between stop-side
and target-side gaps — a reminder that the synthetic numbers are for
verifying *mechanism*, never for reading off a *sign*.

**Confidence: certain** as a code defect (it does contradict "fill at the
market" logic); **not certain, and data-dependent** as a direction claim.

### 5. Overnight bars never examined — CONFIRMED

**Where:** the original filtered the feed to
`WINDOW_START <= time <= TRADE_END` before the loop, so only ~08:30–14:30
bars existed. The real file is near-continuous Globex (~1,098 bars/day).

**What:** a multi-day long could be stopped out at 02:00 and the simulator
would not notice until 08:30 the next session.

**Direction on real data:** costs $513,285 and adds 91 trades (row 4 of
the step table) — the single largest negative correction in the whole
chain. It interacts with #4 (both change how a gap-through-a-level is
priced) so the two should not be read as independent, but unlike #4 this
one has a clean, consistent direction: seeing the overnight bars always
catches a stop closer to its true crossing than waiting for the next
session's open, which is never a better fill for the trader.

**Confidence: certain** as a defect, certain in direction, large in
magnitude.

### 6. Window end double-counted — CONFIRMED, small

**Where:** `WINDOW_START <= row_time <= WINDOW_END` (inclusive) combined
with `TRADE_START <= row_time` where `TRADE_START == WINDOW_END == 10:00`.

**What:** the 10:00 bar both extended the breakout range and was itself
tradable. Since its own high had just been folded into `window_high`,
`row.high > window_high` could never fire on it — the bar was
structurally incapable of triggering an entry.

**Direction:** neutral on optimism; it silently discarded one entry bar
per day.

**Confidence: certain** as a defect, **low** materiality.

### 7. Entries allowed on the session's final bar — CONFIRMED, small

**Where:** no last-bar guard on entries; shorts exit when
`row_time >= TRADE_END`.

**What:** a position opened on the 14:30 bar was closed at that same
bar's close, booking a guaranteed round-turn cost and no price move.

**Direction:** legacy **understates** PnL (adds small certain losses) —
opposite in sign to most items here.

**Confidence: high.**

### 8. Max-hold longs exited at the first bar of day 5 — CHANGED BEHAVIOUR

**Where:** legacy `elif days_in_trade >= MAX_LONG_HOLD_DAYS:` fires on any
bar, so the exit happened on day 5's *first* bar (08:30). The corrected
engine adds `and is_last_bar`, exiting at 14:30 instead.

**Direction:** ambiguous. This extends every max-hold long by ~6 hours.

**Confidence: this is not a bug fix.** Neither convention is wrong; the
original is arguably the more conservative one. The previous rewrite
changed it without documenting it. **Flagged for your decision.**

### 9. Trading-day mapping — CHANGED BEHAVIOUR, materially large (correction to this document)

Bars after the 16:00 maintenance halt now belong to the next session (CME
convention) instead of their plain calendar date.

**This document previously dismissed this as a minor consequence of #5
with "no independent economic content." That was wrong.** On the real
feed, this single step moves net PnL by **+$599,885** and Sharpe by
**+0.085** — the second-largest step in the whole chain, bigger than the
combined net effect of every other correction put together. It changes
which trading day a late-evening bar's entries, exits and day-counts get
attributed to, which shifts `max_long_hold_days` exit timing and the
2-trades-per-day cap for a meaningful share of the sample. It deserves the
same "genuine defect, real materiality" weight as items #4 and #5, not a
footnote.

### 10. `max(1, ...)` position size floor — CONFIRMED defect, immaterial

Legacy `position_size` returned at least 1 contract even when both the
risk budget and the margin cap said zero, i.e. it would trade an account
that cannot afford the position. Never binds at $1M initial capital with a
$2,000 stop. Measured effect on synthetic data: exactly zero.

### 11. Inert `cond1`–`cond4` filter gate — CONFIRMED, one-day effect

`cond1 = cond2 = cond3 = cond4 = True`, guarded by
`prev_summary is not None`. The conditions did nothing. The guard did:
it suppressed all trading on the **first day of the sample**, because no
previous day exists yet. That is the entire behavioural content of the
placeholder block.

### 12. `Sharpe` guard defeated by float noise — found during this audit

`compute_metrics` guarded division with `if std_ret > 0`. A perfectly
smooth equity curve produces `std ~ 1e-16`, not `0`, so Sharpe returned
~1.2e14 instead of `NaN`. Fixed with a `1e-12` floor. **Cannot affect any
published number** — a real daily return series has `std ~ 1e-2` — and a
test now covers it.

---

## What is *not* a bias

**Timezone.** The original README described the window as "pre-market
08:30–10:00 **ET**". The feed is stamped **US/Central**: the volume
diagnostic finds the cash-open spike at 08:30 (×6.4 versus the previous
half hour), and the 16:00–17:00 maintenance halt is in the right place for
CT. So 08:30–10:00 CT = 09:30–11:00 ET — the first 90 minutes of the cash
session. **The code was always right; only the label was wrong.** No
number changes. This was a documentation error, not a simulation bias.

**Roll adjustment.** The roll diagnostic finds 174 gaps beyond 20 points,
57 of them (33%) in quarterly roll months — which is exactly the ~33%
expected by chance from 4 months in 12. That is *absence of evidence* for
an un-adjusted splice, not evidence of a back-adjusted series. Describing
the series as "back-adjusted (verified by the roll diagnostic)" overstates
what the test can show. Confirm with the vendor.

---

## Reproducing this

The market data is not in the repository, so a clone alone cannot
regenerate either result set — but **this has now been run against the
real files and both endpoints match exactly** (previous section). With
the CSVs in place, anyone with the data can reproduce the same check:

```bash
python tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq Data/QQQ_daily.csv
```

This runs the same 693,117 five-minute bars through every intermediate
configuration, one flag at a time, and checks both endpoints against the
published numbers. Expect:

```
[MATCH] legacy trades        got   1,728 | published   1,728
[MATCH] legacy net PnL       got 3,287,392.0 | published 3,287,392.0
[MATCH] legacy Sharpe        got     0.95 | published     0.95
[MATCH] corrected trades     got   1,725 | published   1,725
[MATCH] corrected net PnL    got 2,800,810.0 | published 2,800,810.0
[MATCH] corrected Sharpe     got     1.02 | published     1.02
```

Run without `--nq`/`--qqq` to use deterministic synthetic bars instead.
Synthetic results are useful only for verifying *mechanism and sign
plausibility* on a case-by-case basis — item #4 above is a direct
counterexample of a step whose sign flips between the synthetic run and
the real one. Never quote a synthetic magnitude as if it were a published
number.

The claim that the legacy path *is* the original engine is enforced
independently of any data file by `tests/test_legacy_equivalence.py`,
which contains a literal transcription of the notebook loop and asserts
trade-for-trade equality at both 1% and 2% risk on synthetic bars.
