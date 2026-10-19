# Simulation bias audit

This repository has produced **two** result sets from the same market data.
They differ because the simulator's conventions changed, not because the
strategy changed. This document lists every difference, says which are
genuine defects and which are modelling choices, and attributes the change
in the headline numbers.

> **Scope.** Both result sets come from this repository's engine, which is a
> simplified variant of the MultiCharts strategy, not a port of it (see the
> README's comparison table). "Corrected" below means corrected simulation
> conventions for *this* engine's rules, not agreement with MultiCharts.
> Since this audit was first written, the MultiCharts code has settled one
> open question, the entry fill (it uses a stop order), and exposed a new
> defect, a one-minute bar timestamp shift (item #13). The shift is now
> corrected, and every figure below includes the correction except the
> legacy row, which deliberately reproduces the original notebook.

| | Legacy (original notebook) | Corrected |
|---|---:|---:|
| Net PnL, 1% risk | $3,287,392 | $2,744,225 |
| Trades | 1,728 | 1,724 |
| PnL / contract | $191.33 | $176.08 |
| Sharpe | 0.95 | 1.00 |
| Volatility | 11.25% | 14.18% |
| Max drawdown | 15.06% | 16.68% |
| QQQ benchmark Sharpe | 0.64 | 0.77 |

Both sets are internally consistent: every figure in each README matches
the embedded outputs of the notebook that produced it.

**Verified end to end on the real 10-year feed**:
`tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq
Data/QQQ_daily.csv` reproduces both endpoints exactly —
1,728 trades / $3,287,392.20 / Sharpe 0.9480 (legacy, 693,117 five-minute
bars read as start-stamped, as the original notebook did) and
1,724 trades / $2,744,225.00 / Sharpe 1.0022→1.00 (corrected, 688,976
five-minute bars read as end-stamped) — to the cent and to two Sharpe
decimals. This is not an estimate; every number in the tables below
(except the "not a bias" section, which was always data-derived) comes
from that run.

---

## The headline result: what actually moved the number

Net PnL fell 16.5% ($543,167). Little of that is bias correction —
confirmed on the real feed, not inferred from two endpoints:

| Configuration | Net PnL | Trades | Sharpe |
|---|---:|---:|---:|
| Close-fill entries, legacy conventions, start-stamped bars (as published) | $3,287,392.20 | 1,728 | 0.95 |
| Close-fill entries, legacy conventions, end-stamped bars | $3,646,837.40 | 1,735 | — |
| Close-fill entries, **all corrections applied** | $3,437,125.00 | 1,734 | — |
| Stop-fill entries, all corrections applied | $2,744,225.00 | 1,724 | 1.00 |

Reading down the table:

- **Reading the bars correctly was worth +$359,445.20 (+10.9%)** on the
  legacy conventions (item #13).
- **The eleven bias corrections together were worth −$209,712.40 (−5.8%)
  on PnL**, exact, read directly off the attribution run
  (`3,437,125.00 − 3,646,837.40`). They netted small because they point in
  both directions, not because each one is individually small — see the
  per-step table below, where the biggest single steps move six figures.
- **The entry convention was worth exactly −$692,900.00 (−20.2%)**,
  likewise read directly off the run. Switching from filling at the
  breakout bar's close to filling at the breakout level is a change of
  fill convention, not a defect fix, and it accounts for more than 100%
  of the drop in the headline number. The stop fill is the convention the
  MultiCharts strategy uses (`buy next bar at ... stop`), so it is the
  right default; but the drop comes from that choice, not from the bias
  corrections.

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
| bar timestamps (start → end of bar) | 1,735 | +7 | 3,646,837.40 | +359,445.20 | 0.9979 | +0.0498 |
| cost model ($9.20 → $20.00 RT) | 1,735 | 0 | 3,296,140.00 | −350,697.40 | 0.9482 | −0.0496 |
| gap-aware fills | 1,735 | 0 | 3,555,630.00 | +259,490.00 | 0.9152 | −0.0330 |
| scan overnight bars | 1,822 | +87 | 2,684,775.00 | −870,855.00 | 0.8476 | −0.0676 |
| exclusive window end | 1,850 | +28 | 2,882,040.00 | +197,265.00 | 0.8694 | +0.0218 |
| block last-bar entry | 1,836 | −14 | 2,671,270.00 | −210,770.00 | 0.8407 | −0.0286 |
| max-hold exit at session close | 1,761 | −75 | 2,911,530.00 | +240,260.00 | 0.8712 | +0.0305 |
| CME trading-day mapping | 1,734 | −27 | 3,437,125.00 | +525,595.00 | 0.9413 | +0.0701 |
| min-1-contract / prev-session floor | 1,734 | 0 | 3,437,125.00 | 0.00 | 0.9413 | 0.0000 |
| daily mark-to-market | 1,734 | 0 | 3,437,125.00 | 0.00 | 0.9333 | −0.0080 |
| entry_mode: close → stop | 1,724 | −10 | 2,744,225.00 | −692,900.00 | 0.8312 | −0.1021 |
| drop calendar padding | 1,724 | 0 | 2,744,225.00 | 0.00 | 1.0022 | +0.1710 |

**Caveat on reading individual rows.** The steps are applied cumulatively,
in this order, so a row's Δ is the marginal effect *given every correction
above it already applied* — not that bias's isolated effect against the
untouched legacy baseline. The engine is also path-dependent (position
sizing depends on the running account value, so one changed trade shifts
the size, and sometimes the existence, of every trade after it). Three
consequences worth naming rather than glossing over:

- **`block last-bar entry` moves the wrong way for its own stated
  mechanism.** Removing 14 trades that were guaranteed near-breakeven
  losers (a bad fill with no time to move) should free roughly
  14 × $20 ≈ $280. It actually cost $210,770. That gap is the cascading
  effect: removing those 14 entries changed the account-value path,
  which changed sizing and downstream trade generation for the rest of
  the sample. The defect claim (#7 below) still holds; the *dollar
  figure* attached to it is a chain effect, not the isolated cost of
  those 14 fills.
- **`CME trading-day mapping` is not the minor, derivative item this
  document originally called it.** It moves +$525,595 — the largest
  positive step in the chain, larger in size than the net effect of all
  eleven bias corrections combined. See the correction to item #9 below.
- **Individual steps are fragile; the endpoints are not.** Correcting the
  bar timestamps (item #13) moved the corrected endpoint by only 2.0%,
  but moved single steps by hundreds of thousands of dollars: scanning
  overnight bars went from −$513,285 to −$870,855, and the exclusive
  window end changed sign (−$73,815 to +$197,265). Read the per-step
  figures as orders of magnitude, not exact prices.

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
| QQQ Sharpe | 0.6375 | 0.7685 | 1.2054 | 1.2036 |
| QQQ volatility | 18.36% | 22.11% | 1.2047 | 1.2036 |

Both match the closed form to within 0.2%. The entire benchmark
improvement is this one arithmetic artefact and nothing else.

**Confidence: certain.** This is the single largest driver of the Sharpe
change, and correcting it makes the strategy look *better*, not worse.

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
by a test (`test_cost_delta_per_contract_is_exactly_10_80`). On the real
feed this step costs **$350,697** (row 3 of the step table) — more than
the naive trades × contracts × $10.80 estimate, because lower PnL also
shrinks every later position through the equity-based sizing.

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
side won: turning gap-aware fills on **raised** net PnL by $259,490 (row
4 of the step table). The synthetic-bars run in this repository's test
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

**Direction on real data:** costs $870,855 and adds 87 trades (row 5 of
the step table) — the single largest step in the whole chain. It interacts with #4 (both change how a gap-through-a-level is
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

**On real data:** +28 trades and +$197,265 (row 6 of the step table). That
dollar figure is mostly a chain effect; before the timestamp correction
the same step measured −$73,815.

**Confidence: certain** as a defect; the dollar materiality is not stable.

### 7. Entries allowed on the session's final bar — CONFIRMED, small

**Where:** no last-bar guard on entries; shorts exit when
`row_time >= TRADE_END`.

**What:** a position opened on the 14:30 bar was closed at that same
bar's close, booking a guaranteed round-turn cost and no price move.

**Direction:** legacy **understates** PnL (adds small certain losses) —
opposite in sign to most items here.

**Confidence: high.**

### 8. Max-hold longs exited at the first bar of day 5 — RESOLVED: the corrected behaviour is right

**Where:** legacy `elif days_in_trade >= MAX_LONG_HOLD_DAYS:` fires on any
bar, so the exit happened on day 5's *first* bar (08:30, before the
trading window even opens). The corrected engine adds `and is_last_bar`,
exiting at that session's close (14:30) instead.

**Magnitude on real data:** +$240,260 and +0.031 Sharpe (row 8 of the
step table), and it removes 75 trades — the exit lands on a later bar, so
some positions that used to close and free the slot for a second entry no
longer do.

**Resolution.** This document originally called it "not a bug fix, neither
convention is wrong, flagged for the author's decision." On review that
was too neutral. The documented rule is *"longs can hold up to 5 sessions
to capture follow-through."* `days_in_trade` counts the entry day as day
1, so exiting on the first bar of day 5 captures essentially none of the
fifth session: the position is really held for four complete sessions
plus one overnight. That is an off-by-one against the stated rule, not a
conservative alternative reading of it.

The corrected behaviour — flat at the close of the fifth session — is what
"up to 5 sessions" describes, and is kept as the default. The legacy
timing remains reachable via `SimFlags(max_hold_exit_at_close=False)`
because reproducing the published legacy numbers requires it. Pinned by
`test_max_hold_exits_at_session_close_of_fifth_day`.

**What the MultiCharts code says:** it holds longs for up to *eight*
sessions, not five, so neither five-session variant matches it. The fix
above is right for the rule as this engine states it; matching MultiCharts
needs `max_long_hold_days=8` together with the other rule differences
listed in the README.

### 9. Trading-day mapping — CHANGED BEHAVIOUR, materially large (correction to this document)

Bars after the 16:00 maintenance halt now belong to the next session (CME
convention) instead of their plain calendar date.

**This document previously dismissed this as a minor consequence of #5
with "no independent economic content." That was wrong.** On the real
feed, this single step moves net PnL by **+$525,595** and Sharpe by
**+0.070** — the largest positive step in the chain, bigger than the
combined net effect of the eleven bias corrections. It changes
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

### 13. Bar timestamps off by one minute — CONFIRMED, corrected

**Where:** `load_nq_1min` and `resample_5min` treat each 1-minute
timestamp as the *start* of its bar. MultiCharts exports stamp bars at
their *end*: the minute 08:29–08:30 is written `08:30`, and the first bar
after the 17:00 reopen is written `17:01`.

**What:** if the data file is stamped at bar end, every 5-minute bar is
built from minutes shifted by one. The opening range then includes the
08:29–08:30 minute, before the cash open, and leaves out 09:59–10:00.

**Status:** confirmed on the file behind the published numbers: the first
bar after the 17:00 reopen is stamped 17:01 on 2,509 of 2,510 sessions.
`run_data_diagnostics` reports which convention a file appears to use
(the first bar after the halt, 17:00 or 17:01), and
`load_nq_1min(path, stamped_at='end')` corrects it. The correction is now
the first step of the attribution chain, so the legacy row still
reproduces the original notebook and every row after it uses the right
bars.

**Magnitude on real data:** +$359,445 and +7 trades on the legacy
conventions (row 2 of the step table). Applied to the final configuration
instead, it moves the headline from 1,725 trades / $2,800,810 / PnL per
contract $176.94 / Sharpe 1.0165 to 1,724 trades / $2,744,225 / $176.08 /
Sharpe 1.0022: −2.0% on net PnL. The 5-minute bar count drops from 693,117
to 688,976, almost entirely because the last minute before each daily
halt no longer forms a one-minute "bar" of its own: read as bar start,
the minute stamped 16:00 (2,488 sessions), or 15:15 in the years when
the halt started then (1,552), opened a new 5-minute bucket.

**Confidence: certain** as a defect; small on the headline, large on
individual attribution steps (see the caveat under the step table).

---

## What is *not* a bias

**Timezone.** The original README described the window as "pre-market
08:30–10:00 **ET**". The feed is stamped **US/Central**: the volume
diagnostic finds the cash-open spike at 08:30 (×6.1 versus the previous
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

This loads the file twice — start-stamped for the legacy row, end-stamped
for every row after it — runs every intermediate configuration, one flag
at a time, and checks both endpoints against the published numbers.
Expect:

```
[MATCH] legacy trades        got   1,728 | published   1,728
[MATCH] legacy net PnL       got 3,287,392.0 | published 3,287,392.0
[MATCH] legacy Sharpe        got     0.95 | published     0.95
[MATCH] corrected trades     got   1,724 | published   1,724
[MATCH] corrected net PnL    got 2,744,225.0 | published 2,744,225.0
[MATCH] corrected Sharpe     got     1.0 | published     1.0
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
