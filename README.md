# NQ Intraday Breakout

Opening-range breakout on CME Nasdaq-100 futures (NQ), backtested on ten
years of one-minute data. The repository exists as much for the audit of the
backtest as for the strategy itself: the same rules on the same data produce
two materially different result sets depending on simulation conventions, and
most of this README is about which conventions, and why.

The engine here is a **simplified variant** of a strategy that runs in
MultiCharts, not a faithful port of it. The differences are listed in
[Relation to the MultiCharts strategy](#relation-to-the-multicharts-strategy);
reconciling the two trade by trade is the next piece of work.

[![tests](https://github.com/giovannibrusco/nq-intraday-breakout/actions/workflows/tests.yml/badge.svg)](https://github.com/giovannibrusco/nq-intraday-breakout/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Licence](https://img.shields.io/badge/licence-MIT-blue)

---

## Strategy rules

```
WINDOW   08:30-10:00 CT  (= 09:30-11:00 ET, first 90 minutes of the cash session)
ENTRY    break of the window high (long) or window low (short), 10:00-14:30 CT
         max 2 trades per day
STOP     2,000 USD per contract  (100 index points)
TARGET   4,000 USD per contract  (200 index points)
HOLD     longs up to 5 sessions; shorts always flat by the session close
SIZE     min( risk% x equity / 2,000 , margin cap after a 15% buffer )
```

### Relation to the MultiCharts strategy

The rules above come from a strategy written in MultiCharts. A separate
Python replica of that strategy, checked against the MultiCharts trade
report, matches 96% of its trades. Compared with that reference, this
engine shares the opening range (08:30–10:00 CT), the 2,000 / 4,000 USD
stop and target and the two-entries-per-day cap, and differs in the rest:

| Rule | This engine | MultiCharts strategy |
|---|---|---|
| Entry filters | None | Four conditions, both sides: yesterday's body < 50% of yesterday's range; today not an outside day versus yesterday; today's range > 0.75%; today's open inside yesterday's range |
| Opposite breakout while in a position | Ignored | Reverses the position (about a third of MultiCharts trades start this way) |
| Long holding period | Up to 5 sessions | Up to 8 sessions |
| Short exit | Close of the 14:30 bar (14:35 CT) | Session end, 16:00 CT |
| Re-entry after a target fill | Never on the bar of an exit | Same bar, at the target price, if the level is still broken |
| Stop and reversal on the same bar | n/a (no reversals) | Stop first, if closer |
| Holidays and early closes | Traded | Not traded |
| 10:00–10:05 bar | Tradable | Not traded |
| Entry fill | Stop order at the level, or the open if the bar gaps through (`entry_mode='stop'`) | Same: `buy next bar at ... stop` |

On the same data only about 60% of this engine's entries coincide with the
MultiCharts ones. Every figure below describes **this engine**, not the
MultiCharts strategy.

## Assumptions behind every figure below

Absolute PnL numbers are meaningless without these. They are stated here once
and apply to every table in this document.

| Assumption | Value | Note |
|---|---|---|
| Initial capital | 1,000,000 USD | Fixed; no deposits or withdrawals |
| Contract multiplier | 20 USD per index point | NQ (full-size), not MNQ |
| Tick size / value | 0.25 points / 5 USD | |
| Margin per contract | 23,282.57 USD | Static present-day IB overnight margin, applied to the whole history |
| Margin buffer | 15% | Position capped below the raw margin limit |
| Risk per trade | 1% or 2% of current equity | Both regimes reported |
| Costs, corrected runs | 20.00 USD per contract per round turn | 2.50 USD/side all-in commission + 1.5 ticks/side slippage |
| Costs, legacy run | 9.20 USD per contract per round turn | 0.85 USD/leg broker commission + 1.5 ticks charged once |

Historical margins were materially lower than 23,282.57 USD, so early-sample
sizing under the margin cap is conservative rather than accurate. Slippage is
a flat per-contract amount and does not scale with order size.

## Results

Ten years of NQ, March 2015 – April 2025: 3.4M one-minute bars resampled to
688,976 five-minute bars, with each 1-minute timestamp read as the end of its
bar (the MultiCharts export convention). 1% risk regime.

The stop-fill row is the headline: it is the entry convention the
MultiCharts strategy uses. The other two rows are kept because the audit in
[What moves the number](#what-moves-the-number) explains how they arise.

| Configuration | Sharpe | Volatility | Max DD | t-stat | CAGR | Net PnL (USD) | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| Legacy conventions, close fill | 1.00 | 11.24% | 13.33% | 3.79 | 16.58% | 3,646,837 | 1,735 |
| Corrected conventions, close fill | 1.13 | 14.13% | 13.73% | 3.57 | 16.04% | 3,437,125 | 1,734 |
| **Corrected conventions, stop fill** | **1.00** | **14.18%** | **16.68%** | **3.22** | **14.09%** | **2,744,225** | **1,724** |
| QQQ buy & hold | 0.77 | 22.11% | 35.12% | — | 15.61% | — | — |

The legacy row keeps the original calendar-day equity grid, which
understates Sharpe and volatility by a factor of `sqrt(252/365)`; on that
same grid QQQ scores 0.64, not 0.77. The other rows and the benchmark use
trading days. The original notebook, which also read the timestamps as bar
start, published 0.95 and 3,287,392 USD for the legacy row. See
[What moves the number](#what-moves-the-number).

The 2% risk regime doubles position size without changing the trade list, so
per-contract economics and the t-stat are identical to the 1% regime:

| Configuration (2% risk) | Sharpe | Volatility | Max DD | CAGR | Net PnL (USD) | Avg contracts |
|---|---:|---:|---:|---:|---:|---:|
| Legacy conventions, close fill | 0.99 | 23.45% | 26.48% | 34.80% | 18,876,321 | 49.33 |
| Corrected conventions, stop fill | 1.00 | 29.50% | 32.55% | 28.70% | 11,510,315 | 37.03 |

Sub-periods, split on 31 January 2022 (corrected conventions, stop fill).
This is a split of one backtest, not an out-of-sample test — see
[Limitations](#limitations):

| Segment | Sharpe | Volatility | Max DD | CAGR |
|---|---:|---:|---:|---:|
| Stop fill, 2015-03 → 2022-01 | 0.91 | 10.36% | 16.68% | 9.26% |
| Stop fill, 2022-01 → 2025-04 | 1.20 | 20.07% | 16.09% | 24.61% |
| QQQ, 2015-03 → 2022-01 | 0.97 | 21.23% | 28.56% | 20.03% |
| QQQ, 2022-01 → 2025-04 | 0.35 | 23.87% | 29.56% | 5.63% |

The t-stat tests mean per-contract PnL against zero, net of costs. It treats
trades as independent draws; overlapping multi-day longs and the
two-trades-per-day cap both violate that, so it is an optimistic bound rather
than a clean significance test.

<div align="center">
<img src="docs/images/equity_curve.png" width="820" alt="Daily mark-to-market equity versus QQQ buy and hold"/>
<img src="docs/images/drawdown.png" width="820" alt="Drawdown comparison versus QQQ buy and hold"/>
</div>

## What moves the number

An earlier rewrite of this repository replaced the original results with the
corrected ones and described the change as the removal of simulation
shortcuts. Running both engines on the same bars, one convention at a time,
shows that is not what happened.

Net PnL fell from 3,287,392 to 2,744,225 USD (−543,167, −16.5%). Twelve
corrections were applied: one to how the data file is read, eleven to the
simulation conventions.

- **The data fix is worth +359,445 USD on the legacy conventions.** The file
  stamps each minute at its end and the original notebook read it as the
  start, so every 5-minute bar was off by one minute.
- **The eleven convention fixes together are worth −209,712 USD, about
  −5.8%.** They largely cancel: some were optimistic, some pessimistic, and
  the biggest move six figures in each direction.
- **A single fill convention is worth −692,900 USD, about −20.2%**, more than
  the whole drop: whether a breakout order is assumed to fill at the closing
  price of the bar that broke the range, or at the breakout level itself. The
  original notebook used the first; the MultiCharts strategy uses the second.

| Step (cumulative, 1% risk) | Net PnL (USD) | Δ PnL | Sharpe |
|---|---:|---:|---:|
| Legacy, as originally published | 3,287,392 | — | 0.948 |
| Bar timestamps read as bar end | 3,646,837 | +359,445 | 0.998 |
| Cost model, 9.20 → 20.00 USD per round turn | 3,296,140 | −350,697 | 0.948 |
| Gap-aware fills | 3,555,630 | +259,490 | 0.915 |
| Scan overnight bars | 2,684,775 | −870,855 | 0.848 |
| Exclusive window end | 2,882,040 | +197,265 | 0.869 |
| Block last-bar entry | 2,671,270 | −210,770 | 0.841 |
| Max-hold exit at session close | 2,911,530 | +240,260 | 0.871 |
| CME trading-day mapping | 3,437,125 | +525,595 | 0.941 |
| Sizing floor and prev-session gate | 3,437,125 | 0 | 0.941 |
| Daily mark-to-market | 3,437,125 | 0 | 0.933 |
| Entry convention, close → stop fill | 2,744,225 | −692,900 | 0.831 |
| Trading-day equity grid (drop calendar padding) | 2,744,225 | 0 | 1.002 |

Steps are applied cumulatively in this order, so each delta is marginal given
everything above it. The engine is path-dependent — position size follows the
running account value, so one changed trade shifts the size of every
subsequent trade — which is why removing fourteen near-breakeven trades
costs far more than those fourteen trades' own commissions, and why the data
fix is worth +359,445 USD at the top of the chain but only −56,585 USD when
applied to the final configuration (2,800,810 → 2,744,225).

Two results are worth isolating.

**The benchmark improved without its data changing.** QQQ's Sharpe moves from
0.6375 to 0.7685 and its volatility from 18.36% to 22.11% between the two
runs. The QQQ price series is identical in both. The entire difference is that
the original padded the equity curve to all 365 calendar days while still
annualising with 252 trading days. The ratio is 1.2054 against a closed-form
`sqrt(365/252) = 1.2036` — a match to 0.15%, and confirmation that this one
arithmetic artefact explains the whole move. It made the strategy look *worse*,
not better.

**The entry convention is now resolved.** Close fills beat stop fills on
this data (Sharpe 1.13 against 1.00), and an earlier version of this README
reported the two as a band because it was not known which one the original
strategy used. The MultiCharts code places a stop order for the next bar
(`buy next bar at ... stop`), filled at the level or at the open if the bar
gaps through it. That is `entry_mode='stop'`, the default, and the close-fill
figure is no longer a candidate result.

Full detail, including which items are genuine defects and which are changed
assumptions, is in **[docs/bias-audit.md](docs/bias-audit.md)**.

## Findings

- Under corrected conventions and the stop-fill entry the MultiCharts
  strategy uses, this engine's Sharpe is 1.00. Filling at the bar close
  instead would report 1.13: one fill assumption is worth about thirteen
  Sharpe points on its own.
- Per-contract PnL is identical across the 1% and 2% risk regimes. This is a
  mechanical identity of the simulator — the same fills, scaled — and says
  nothing about whether the fills would survive at the ~37-contract average
  size the 2% regime reaches.
- Sharpe after January 2022 is higher than before it (0.91 → 1.20) while
  QQQ's fell from 0.97 to 0.35 over the same window. This is a sub-period
  comparison, not out-of-sample evidence: see [Limitations](#limitations).
- Fixing the one-minute timestamp shift barely moves the headline (Sharpe
  1.02 → 1.00, net PnL −2.0%) but moves individual steps of the attribution
  by hundreds of thousands of dollars: scanning overnight bars went from
  −513,285 to −870,855 USD. Per-step attributions on a path-dependent engine
  are fragile, and should be read as orders of magnitude, not exact prices.
- Nearly all of the edge comes from longs: 332.71 USD per contract net of
  costs on 877 longs, against 13.90 USD on 847 shorts. The shorts are close
  to breakeven after costs. Whether that comes from the rule asymmetry
  (multi-session longs, same-day shorts) or from a rising decade has not
  been tested here.
- Doubling the risk budget roughly doubles both volatility and maximum
  drawdown, leaving Sharpe unchanged, as linear scaling implies.
- The largest single driver of the reported Sharpe was never the strategy: it
  was whether the equity curve was sampled on trading days or calendar days.

## Repository layout

```
src/nq_breakout/
    data.py          CSV loading, 1-minute to 5-minute resampling
    backtest.py      event-driven engine: entries, exits, sizing, costs
    metrics.py       Sharpe, CAGR, drawdown, volatility, t-stat
    diagnostics.py   feed checks: timezone, roll splicing, gap exposure
    plots.py         equity, drawdown and per-trade figures
tests/               test suite; no market data required
tools/
    bias_attribution.py   runs both engines flag by flag
    make_figures.py       regenerates the two README figures
docs/
    bias-audit.md    every convention difference, one at a time
data/README.md       expected CSV schema and feed properties
NQ_breakout.ipynb    narrative layer; imports the package
```

Simulation conventions are flags on `SimFlags`, not hard-coded behaviour.
`SimFlags()` gives the corrected conventions; `LEGACY_FLAGS` together with
`LEGACY_CONFIG_KWARGS` reproduces the original notebook exactly.

## Quickstart

```bash
git clone https://github.com/giovannibrusco/nq-intraday-breakout.git
cd nq-intraday-breakout
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,notebook]"

python -m pytest          # full suite; no data files needed
```

With market data in place (see [data/README.md](data/README.md)):

```bash
python tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq Data/QQQ_daily.csv
jupyter lab               # then run NQ_breakout.ipynb
```

The attribution script reproduces every figure in this README and asserts both
endpoints against the published numbers. Read the diagnostics output before
trusting any metric: if the timezone, bar-timestamp or roll checks warn, the
data is not what the engine assumes. The data file is stamped at bar end (the
MultiCharts export convention), so the script and the notebook load it with
`load_nq_1min(path, stamped_at='end')`. `tools/make_figures.py`, with the same
`--nq` and `--qqq` arguments, regenerates the two figures.

## Data

Market data is not committed. Two files are expected in `Data/`:
`nq-10y-1min.csv` (front-month continuous NQ, 1-minute bars) and
`QQQ_daily.csv` (daily benchmark closes). The full schema, the timezone and
back-adjustment properties the engine relies on, and how to verify a file
before trusting results, are documented in [data/README.md](data/README.md).

The vendor is not currently recorded and is marked as an open item in that
file. Results that cannot be traced to a named feed cannot be independently
checked. The file's timestamps mark the *end* of each minute, and every
figure in this README is computed with that reading.

## Limitations

- **This is not the MultiCharts strategy.** Eight rules differ (see
  [the comparison table](#relation-to-the-multicharts-strategy)), and only
  about 60% of entries coincide. The results here should not be read as the
  track record of the strategy that runs in MultiCharts.
- **Earlier figures used bars shifted by one minute.** The data file is
  stamped at bar end (MultiCharts convention): the first bar after the 17:00
  reopen is written 17:01 on 2,509 of 2,510 sessions. Earlier versions of
  this README read each timestamp as the start of its bar, so every
  5-minute bar was off by a minute and the opening range included the
  08:29–08:30 minute. All figures here are now computed with the correct
  reading; the correction moved the headline from Sharpe 1.02 and
  2,800,810 USD to 1.00 and 2,744,225 USD.
- **The sample ends in April 2025.** Nothing after that date is reported here.
- **No partial fills.** Every order is filled complete at a single price. At
  the ~37-contract average size of the 2% regime this is optimistic.
- **Slippage is a static 1.5 ticks per side.** It does not vary with
  volatility, time of day, order size, or the gap through the level being
  crossed. Stress at 2–4 ticks before trusting the 2% regime.
- **No volatility scaling.** Stops and targets are fixed dollar amounts for
  the whole decade. 2,000 USD was 2.3% of the index in 2015 and 0.5% in 2025,
  so the strategy is materially different at the two ends of the sample.
- **No session filters are active.** Every breakout in the window is taken,
  subject only to the two-per-day cap. There is no volatility, trend, or macro
  calendar filter.
- **Parameter choices are in-sample.** The 90-minute window, the 2,000/4,000
  stop and target, the five-session hold and the two-trade cap were all set
  when the rules were written. Nothing here searched a parameter grid, but
  nothing establishes that these values were not themselves the product of
  earlier search.
- **There is no out-of-sample period for this engine.** The rules were frozen
  in MultiCharts, and this engine differs from them, so the freeze date does
  not make any part of this sample out-of-sample for it. The freeze date
  itself is also uncertain: earlier versions of this README said January 2022,
  while the MultiCharts version has been treated as out-of-sample from 2021.
- **Static present-day margin is applied across the whole history**, and no
  intraday margin or exchange halt is modelled.
- **The continuous contract is back-adjusted, which distorts price levels.**
  Back-adjustment keeps point differences correct, which is what fixed-point
  stops and targets need, but early prices are far from the traded ones (on
  another copy of the MultiCharts history, the 2010 series is about 2.2 times
  the actual price). Any threshold
  expressed as a percentage of price, such as the MultiCharts filters'
  0.75% range, is distorted early in the sample.
- **The t-stat assumes independent trades**, which overlapping multi-day longs
  violate.

## Roadmap

1. **Reconcile with the MultiCharts strategy.** The MultiCharts code has been
   found and settled the entry fill (stop order). Next: add the missing rules
   (reversals, 8-session longs, 16:00 short exit, same-bar re-entry, holiday
   and early-close calendar) as `SimFlags`, and match this engine trade by
   trade against the MultiCharts trade report.
2. **The four MultiCharts entry filters.** The original notebook carried four
   placeholder conditions (`cond1`–`cond4`) hard-coded to `True`; they have
   been removed. The actual MultiCharts conditions are now known (see
   [the comparison table](#relation-to-the-multicharts-strategy)) and are part
   of the reconciliation above.
3. **Extend the sample past April 2025.**
4. **Execution realism.** Limit-order logic, depth-aware fills, and slippage
   that scales with order size, to replace the static tick assumption.
5. **Volatility-scaled stops**, to address the non-stationarity of fixed
   dollar risk across a decade.
6. **Capital efficiency.** CME SPAN margin instead of a static broker figure,
   and return measured on margin rather than on notional capital.
7. **Robustness testing.** Block bootstrap and parameter perturbation, to put
   an interval around the reported Sharpe rather than a point.

## Licence

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is for educational and research purposes only. It is not
financial advice. Past performance does not guarantee future results. Futures
trading involves substantial risk of loss, and leverage amplifies both gains
and losses. The model assumes continuous market access and ignores exchange
halts. Verify all data against proprietary feeds before deploying capital.
