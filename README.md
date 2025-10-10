# NQ Breakout Strategy

Systematic breakout model for CME Nasdaq futures (`NQ`) built for a prop-trading style interview. The notebook reverse-engineers how the strategy behaves, quantifies edge versus a QQQ buy & hold proxy, and surfaces the operational constraints (margins, risk budget, slippage) a desk such as Jane Street or Optiver would scrutinise.

## Strategy Overview
- **Universe & Horizon**: Front-month NQ futures, March 2015 – April 2025, aggregated to 5-minute bars from raw 1-minute data.
- **Benchmark**: Daily QQQ closes to provide an equity index proxy with comparable capital usage.
- **Session Logic**: Pre-market window `08:30–10:00 ET` anchors the breakout range. Trades are only allowed between `10:00–14:30 ET`.
- **Execution Rules**:
  - Long when price breaks above the session window high; short when it breaks below the window low.
  - Longs can hold up to 5 sessions to capture follow-through; shorts are flattened by 14:30 ET to avoid overnight borrow and exchange margin jumps.
  - Fixed stop/target at \$2,000 / \$4,000 per contract (100 / 200 index points with \$20 point value) to keep risk symmetrical and constrain tail losses.
- **Risk Management**:
  - Dynamic position sizing with the lesser of (i) percentage-of-equity risk budget and (ii) available margin after a 15% buffer.
  - Commission of \$0.85 per leg (Interactive Brokers schedule) and 1.5 ticks of slippage per side (\$5/tick) debited on every round turn.

## Backtest Results
The notebook reports two scaling regimes using identical fills and trade list; only the risk budget changes.

| Risk Budget per Trade | Net PnL (USD) | Trades | Avg Contracts | Sharpe | Volatility | Max Drawdown | Max Drawdown % | CAGR |
|----------------------:|--------------:|-------:|---------------:|-------:|-----------:|--------------:|---------------:|-----:|
| 1% of equity          | 3,287,392     | 1,728  | 9.37           | 0.95   | 11.25%     | -361,027      | 15.06%         | 15.65% |
| 2% of equity          | 16,358,914    | 1,728  | 45.58          | 0.96   | 23.44%     | -2,884,377    | 29.51%         | 32.98% |
| QQQ buy & hold        | —             | —      | —              | 0.64   | 18.36%     | -1,417,742    | 35.12%         | 15.74% |

Segmenting on **31 Jan 2022** to mimic an in-sample / out-of-sample split:

| Period (1% risk regime) | Sharpe | Volatility | Max DD | Max DD % | CAGR |
|-------------------------|-------:|-----------:|-------:|---------:|-----:|
| 2015-03-29 – 2022-01-30 | 0.90   | 8.69%      | -218,605 | 14.88% | 11.43% |
| 2022-01-31 – 2025-04-04 | 1.06   | 15.34%     | -361,027 | 15.06% | 24.57% |

| Period (2% risk regime) | Sharpe | Volatility | Max DD | Max DD % | CAGR |
|-------------------------|-------:|-----------:|-------:|---------:|-----:|
| 2015-03-29 – 2022-01-30 | 0.91   | 18.35%     | -953,889 | 29.03% | 24.17% |
| 2022-01-31 – 2025-04-04 | 1.07   | 31.70%     | -2,884,377 | 29.51% | 52.35% |

Key takeaways a prop desk cares about:
- Edge persists out-of-sample with higher Sharpe than the benchmark despite leverage scaling.
- Drawdown doubles when doubling the risk budget, but remains inside a 30% loss-of-equity envelope.
- Per-contract economics (\$191 average) remain stable regardless of scaling, indicating linear exposure rather than liquidity decay.

## Methodology Notes
- Data is stored under `Data/` (`nq-10y-1min.csv`, `QQQ_daily.csv`). The code resamples intraday data to 5-minute bars on the fly.
- Breakout logic currently runs without additional session filters (`cond1–cond4` placeholders). They are kept in the code to inject signals such as overnight drift, prior-day range or macro overlays.
- Fills are assumed at the close of the breakout bar. No partial fills, volatility scaling, or spread-based execution are modelled—intended as a first-pass stress test before plugging into a limit-order simulator.
- Long trades can span multiple days; the bookkeeping keeps day indices to compute holding period and equity curves reliably.
- The comparison to QQQ assumes full utilisation of initial capital and daily close-to-close compounding to mimic a passive ETF allocation.

## Repository Structure
- `NQ_breakout.ipynb` — core backtest, analytics dashboards, and performance attribution.
- `QQQ_bias.ipynb`, `prova.ipynb` — work-in-progress notebooks for auxiliary signals (not part of the featured analysis).
- `Data/` — raw and derived market data used by the notebook.
- `ssrn-4416622.pdf` — reference research paper that inspired the investigation.

## Reproduction Guide
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pandas numpy matplotlib jupyter
jupyter lab
```
Open `NQ_breakout.ipynb`, run all cells, and ensure the `Data/` folder is present at the repository root. The notebook will raise clear exceptions if required files are missing or columns need renaming.

## Extensions & Interview Talking Points
1. **Execution realism** — add limit order logic, use depth data, or replay futures book to stress-test slippage assumptions beyond the static 1.5 ticks.
2. **Regime filters** — activate the placeholder conditions to condition breakouts on prior-day trend, macro calendar, or QQQ vs NQ spread, reducing the 1,728 naive trades.
3. **Capital efficiency** — integrate CME SPAN margin models or optimise the margin buffer to show return on capital vs initial margin.
4. **Risk tooling** — extend the metrics block to output exposure by time-of-day, stress scenarios, or Monte Carlo block-bootstraps, which aligns with prop risk reviews.

## Disclaimers
- Historical performance is not indicative of future results; the model assumes continuous market access and ignores exchange halts.
- Data is sourced from historical downloads; verify against proprietary feeds before deploying capital.
- The notebook is designed for research discussion and is not a production-ready trading system.
