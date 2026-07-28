<div align="center">

# 📈 NQ Intraday Breakout
### Systematic Opening-Range Breakout on Nasdaq-100 Futures

*First 90 minutes of the cash session set the levels · stop orders ride the break · longs run up to 5 sessions, shorts flat by the close*

[![tests](https://github.com/giovannibrusco/nq-intraday-breakout/actions/workflows/tests.yml/badge.svg)](https://github.com/giovannibrusco/nq-intraday-breakout/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Backtest](https://img.shields.io/badge/Backtest-2015--2025-informational)
![Sharpe](https://img.shields.io/badge/Sharpe-1.02--1.11%20(band)-success)
![Rules](https://img.shields.io/badge/Rules%20Frozen-2022%20·%20MultiCharts-8A2BE2)
![Licence](https://img.shields.io/badge/Licence-MIT-blue)

<img src="docs/images/equity_curve.png" width="850" alt="Daily mark-to-market equity versus QQQ buy and hold"/>

</div>

---

## 📌 TL;DR

A breakout above (below) the range set in the **first 90 minutes of the CME cash session** goes long (short) NQ with a fixed \$2,000 stop and \$4,000 target per contract. Longs get up to 5 sessions; shorts are always flat by the close. The rules were written in MultiCharts in early 2022 and frozen since.

The Sharpe is quoted as a **band, not a point** — 1.02 to 1.11 — because one simulation assumption that has never been reconciled against the original strategy code is worth roughly 15% of net PnL. Most of this README is about that: which conventions move the result, by how much, and which of them are genuine defects rather than choices.

## ⚙️ Strategy Rules

```
WINDOW   08:30–10:00 CT  (= 09:30–11:00 ET, first 90 minutes of the cash session)
ENTRY    break of the window high (long) or window low (short) · 10:00–14:30 CT
         max 2 trades/day
STOP     $2,000/contract (100 pts)        TARGET   $4,000/contract (200 pts)
HOLD     longs up to 5 sessions · shorts always flat by the session close
SIZE     min( risk% × equity / $2,000 , margin cap after a 15% buffer )
```

## 📐 Assumptions

Absolute PnL is meaningless without these. Stated once; they apply to every table below.

| Assumption | Value | Note |
|---|---|---|
| Initial capital | \$1,000,000 | Fixed; no deposits or withdrawals |
| Contract multiplier | \$20 per index point | NQ full-size, **not** MNQ |
| Tick size / value | 0.25 pts / \$5 | |
| Margin per contract | \$23,282.57 | Static present-day IB overnight margin, applied to all history |
| Margin buffer | 15% | Position capped below the raw margin limit |
| Risk per trade | 1% or 2% of current equity | Both regimes reported |
| Costs — corrected runs | **\$20.00** / contract / round turn | \$2.50/side all-in commission + 1.5 ticks/side slippage |
| Costs — legacy run | **\$9.20** / contract / round turn | \$0.85/leg broker commission + 1.5 ticks charged **once** |

Historical margins were materially lower than \$23,282.57, so early-sample sizing under the margin cap is conservative rather than accurate. Slippage is flat per contract and does not scale with order size.

## 📊 Results

Ten years of NQ, Mar 2015 – Apr 2025 · 3.4M one-minute bars → 693,117 five-minute bars · 1% risk regime.

Three configurations are reported because the choice between them is **not settled** — see [What Moves the Number](#-what-moves-the-number).

| Configuration | Sharpe | Volatility | Max DD | t-stat | CAGR | Net PnL | Trades |
|---|:---:|:---:|:---:|:---:|:---:|---:|---:|
| ⚪ Legacy conventions · close fill | 0.95 | 11.25% | −15.06% | 3.63 | 15.65% | \$3,287,392 | 1,728 |
| 🟢 Corrected conventions · close fill | **1.11** | 14.11% | −17.73% | 3.49 | 15.74% | \$3,323,800 | 1,736 |
| 🟢 Corrected conventions · stop fill | **1.02** | 14.12% | −16.82% | 3.24 | 14.26% | \$2,800,810 | 1,725 |
| 🔵 QQQ buy & hold | 0.77 | 22.11% | −35.12% | — | 15.61% | — | — |

The 2% regime doubles size without changing the trade list, so per-contract economics and the t-stat are identical:

| Configuration (2% risk) | Sharpe | Volatility | Max DD | CAGR | Net PnL | Avg contracts |
|---|:---:|:---:|:---:|:---:|---:|:---:|
| ⚪ Legacy · close fill | 0.96 | 23.44% | −29.51% | 32.98% | \$16,358,914 | 45.6 |
| 🔴 Corrected · stop fill | **1.02** | 29.43% | −32.12% | 29.26% | \$12,069,455 | 39.7 |

Splitting on **31 Jan 2022**, the rule-freeze date (corrected conventions):

| Segment | Sharpe | Volatility | Max DD | CAGR |
|---|:---:|:---:|:---:|:---:|
| 🟢 Stop fill · 2015-03 → 2022-01 | 1.03 | 10.27% | −16.42% | 10.58% |
| 🟢 Stop fill · **2022-01 → 2025-04** | **1.10** | 20.03% | −16.82% | 22.12% |
| ⚪ Close fill · 2015-03 → 2022-01 | 0.87 | 10.29% | −17.73% | 8.75% |
| ⚪ Close fill · **2022-01 → 2025-04** | **1.49** | 19.98% | −16.30% | 31.89% |
| 🔵 QQQ · 2015-03 → 2022-01 | 0.97 | 21.23% | −28.56% | 20.03% |
| 🔵 QQQ · **2022-01 → 2025-04** | 0.35 | 23.85% | −29.56% | 5.63% |

<div align="center">
<img src="docs/images/drawdown.png" width="850" alt="Drawdown comparison versus QQQ buy and hold"/>
</div>

The t-stat tests mean per-contract PnL against zero, net of costs. It treats trades as independent draws; overlapping multi-day longs and the two-trades-per-day cap both violate that, so it is an **optimistic bound**, not a clean significance test.

## 🔬 What Moves the Number

An earlier rewrite of this repository replaced the original results with the corrected ones and described the change as the removal of simulation shortcuts. Running both engines over the same bars, one convention at a time, shows that is not what happened.

Eleven corrections were applied. **Together they are worth +\$36,408 — about +1.1%.** They nearly cancel: some were optimistic, some pessimistic, of comparable size. **The remaining −\$522,990, about −15.7%, comes from a single choice that is not a bug fix at all** — whether a breakout order fills at the *close of the bar that broke the range*, or at the *breakout level itself*.

| # | Step (cumulative · 1% risk) | Net PnL | Δ PnL | Sharpe |
|---|---|---:|---:|:---:|
| 0 | Legacy — all original conventions | \$3,287,392 | — | 0.948 |
| 1 | 💸 Cost model \$9.20 → \$20.00 per round turn | \$3,019,805 | −\$267,587 | 0.910 |
| 2 | 📉 Gap-aware fills | \$3,247,105 | +\$227,300 | 0.873 |
| 3 | 🌙 Scan overnight bars | \$2,733,820 | −\$513,285 | 0.855 |
| 4 | 🕰️ Exclusive window end | \$2,660,005 | −\$73,815 | 0.836 |
| 5 | 🚫 Block last-bar entry | \$2,582,100 | −\$77,905 | 0.830 |
| 6 | 📅 Max-hold exit at session close | \$2,723,915 | +\$141,815 | 0.842 |
| 7 | 🗓️ CME trading-day mapping | \$3,323,800 | +\$599,885 | 0.927 |
| 8 | ⚖️ Sizing floor and prev-session gate | \$3,323,800 | \$0 | 0.927 |
| 9 | 📈 Daily mark-to-market | \$3,323,800 | \$0 | 0.919 |
| 10 | 🎯 **Entry convention · close → stop fill** | \$2,800,810 | **−\$522,990** | 0.843 |
| 11 | 🧮 Trading-day equity grid (drop calendar padding) | \$2,800,810 | \$0 | **1.017** |

> ⚠️ Steps are **cumulative** in this order, so each delta is marginal given everything above it. The engine is path-dependent — position size follows the running account value, so one changed trade shifts the size of every subsequent trade. That is why removing ten near-breakeven trades (step 5) costs far more than those ten trades' own commissions.

Two results worth isolating:

- 🔵 **The benchmark improved without its data changing.** QQQ's Sharpe moves 0.6375 → 0.7683 and its volatility 18.36% → 22.11% between the two runs, on an *identical* price series. The original padded the equity curve to all 365 calendar days while annualising with 252 trading days. Observed ratio **1.2052** against a closed-form `sqrt(365/252) = 1.2036` — a match to 0.13%, confirming this one arithmetic artefact explains the entire move. It made the strategy look **worse**, not better.
- 🎯 **The entry convention is unresolved.** Close fills beat stop fills on this data (Sharpe 1.11 vs 1.02 overall; 1.49 vs 1.10 out-of-sample). Recovering the original MultiCharts source would settle which one the 2022 rules actually used. Until then, the honest reading is a band.

📄 Full detail — twelve differences, which are genuine defects and which are changed assumptions — in **[`docs/bias-audit.md`](docs/bias-audit.md)**.

## 🔍 Findings

- Corrected-convention Sharpe lands between **1.02 and 1.11** depending on an entry-fill assumption never reconciled against the original code. Any single Sharpe quoted here carries roughly a ten-point band from that assumption alone.
- Per-contract PnL is **identical** across the 1% and 2% regimes. That is a mechanical identity of the simulator — same fills, scaled — and says nothing about whether those fills survive at the ~40-contract average the 2% regime reaches.
- Out-of-sample Sharpe exceeds in-sample under **both** fill conventions (1.03 → 1.10 stop, 0.87 → 1.49 close) while QQQ fell from 0.97 to 0.35 over the same window. The rules were not modified in either period — but see [Limitations](#-limitations) on what "out-of-sample" can mean here.
- Doubling the risk budget roughly doubles volatility and max drawdown, leaving Sharpe unchanged, as linear scaling implies.
- The largest single driver of the reported Sharpe was never the strategy: it was whether the equity curve was sampled on **trading days or calendar days**.

## 🗂️ Repository Structure

```
├── 📦 src/nq_breakout/
│   ├── data.py             CSV loading · 1-min → 5-min resampling
│   ├── backtest.py         event-driven engine · entries · exits · sizing · costs
│   ├── metrics.py          Sharpe · CAGR · drawdown · volatility · t-stat
│   ├── diagnostics.py      feed checks: timezone · roll splicing · gap exposure
│   └── plots.py            equity · drawdown · per-trade figures
├── 🧪 tests/               full suite; no market data required
├── 🔧 tools/               bias_attribution.py — runs both engines flag by flag
├── 📄 docs/bias-audit.md   the twelve convention differences, one at a time
├── 🧾 docs/reference-run.txt  recorded real-data run; diff against it to verify
├── 💾 data/README.md       CSV schema · feed properties · provenance
└── 📓 NQ_breakout.ipynb    narrative layer; imports the package
```

Simulation conventions are **flags on `SimFlags`**, not hard-coded behaviour. `SimFlags()` gives the corrected conventions; `LEGACY_FLAGS` with `LEGACY_CONFIG_KWARGS` reproduces the original notebook exactly. `tests/test_legacy_equivalence.py` holds a literal transcription of the original notebook loop and asserts trade-for-trade equality against the packaged legacy path — so that claim is enforced without needing the data files.

## 🚀 Quickstart

```bash
git clone https://github.com/giovannibrusco/nq-intraday-breakout.git
cd nq-intraday-breakout
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,notebook]"

python -m pytest          # full suite; no data files needed
```

With market data in place (see [`data/README.md`](data/README.md)):

```bash
python tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq Data/QQQ_daily.csv
jupyter lab               # then run NQ_breakout.ipynb
```

The attribution script reproduces every figure above and asserts both endpoints against the published numbers, exiting non-zero if any fails to reproduce. A recorded run is kept in [`docs/reference-run.txt`](docs/reference-run.txt) — diff your output against it. **Read the diagnostics output first**: if the timezone or roll checks warn, the data is not what the engine assumes.

## 💾 Data

Market data is **not** committed. Two files are expected in `Data/`:

- **NQ futures** — front-month continuous, 1-minute bars, Mar 2015 – Apr 2025, near-full Globex session, Central Time → `Data/nq-10y-1min.csv`
- **QQQ daily** — benchmark closes → `Data/QQQ_daily.csv`

`load_nq_1min` reads `.csv` and `.csv.gz` transparently. Full schema, the timezone and back-adjustment properties the engine relies on, and how to verify a file before trusting results: [`data/README.md`](data/README.md).

## ⚠️ Limitations

- 🔶 **Entry fills are an open assumption.** The legacy engine fills at the close of the bar that broke the range. Neither that nor the stop-fill alternative has been reconciled against the original MultiCharts code, and the choice is worth ~15% of net PnL.
- 🔶 **No partial fills.** Every order fills complete at a single price. At the ~40-contract average of the 2% regime this is optimistic.
- 🔶 **Slippage is a static 1.5 ticks per side.** It does not vary with volatility, time of day, order size, or the size of the gap through the level. Stress at 2–4 ticks before trusting the 2% regime.
- 🔶 **No volatility scaling.** Stops and targets are fixed dollar amounts for a decade: \$2,000 was 2.3% of the index in 2015 and 0.5% in 2025, so the strategy is materially different at the two ends of the sample.
- 🔶 **No session filters are active.** Every breakout in the window is taken, subject only to the two-per-day cap. No volatility, trend or macro-calendar overlay.
- 🔶 **Parameter choices are in-sample.** The 90-minute window, the \$2,000/\$4,000 stop and target, the five-session hold and the two-trade cap were all set when the rules were written. Nothing here searched a grid — but nothing establishes those values were not themselves the product of earlier search.
- 🔶 **The out-of-sample claim rests on provenance, not on this repository.** Git history starts in 2025; the 2022 rule-freeze is asserted from a MultiCharts workspace not included here.
- 🔶 **Static present-day margin** is applied across all history, and no intraday margin or exchange halt is modelled.
- 🔶 **Back-adjustment is assumed, not proven.** The roll diagnostic finds no clustering of large gaps in roll months — but absence of evidence is not evidence of adjustment.
- 🔶 **The t-stat assumes independent trades**, which overlapping multi-day longs violate.

## 🛣️ Roadmap

1. 🎓 **Recover the original MultiCharts source.** Highest-value open item: it would settle the entry-fill convention, validate the port trade-by-trade, and turn the out-of-sample claim from asserted into documented.
2. 🔀 **Regime and session filters.** The original notebook carried four placeholder conditions (`cond1`–`cond4`) hard-coded to `True` that filtered nothing; they have been removed rather than left as dead branches. The intended use was conditioning entries on prior-day range, overnight drift, or a macro calendar. **That work has not been done.**
3. 📖 **Execution realism.** Limit-order logic, depth-aware fills, and slippage that scales with order size, replacing the static tick assumption.
4. 📏 **Volatility-scaled stops**, to address the non-stationarity of fixed dollar risk across a decade.
5. 🏦 **Capital efficiency.** CME SPAN margin instead of a static broker figure; return measured on margin rather than notional capital.
6. 🎲 **Robustness testing.** Block bootstrap and parameter perturbation, to put an interval around the reported Sharpe rather than a point.

## 🎓 Provenance

The trading rules were designed in **early 2022** on MultiCharts and have not been modified since; this repository is a faithful-in-spirit Python re-implementation whose fill conventions are stricter than — and not yet diffed against — the original. The post-2022 segment is therefore out-of-sample **conditional on that claim**. Recovering the original `.pla`/EasyLanguage source, with its file dates, is the single highest-value pending task.

## 📄 Licence

MIT — see [`LICENSE`](LICENSE).

## ⚖️ Disclaimer

This project is for **educational and research purposes only**. It is not financial advice. Past performance does not guarantee future results. Futures trading involves substantial risk of loss and leverage amplifies both gains and losses. The model assumes continuous market access and ignores exchange halts; verify all data against proprietary feeds before deploying capital.
