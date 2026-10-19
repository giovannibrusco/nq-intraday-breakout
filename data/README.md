# Data

Market data is **not** committed. Place the two CSVs below in a `Data/`
folder at the repository root (capital `D` — that is what the notebook
loads) and they will be picked up. `Data/` is git-ignored.

## Provenance

What the file itself establishes:

- **Export format: MultiCharts.** The column set
  (`Date`, `Time`, `Open`, `High`, `Low`, `Close`, `TotalVolume`) with
  day-first `dd/mm/yyyy` dates is MultiCharts' CSV export layout;
  `TotalVolume` in particular is a MultiCharts field name rather than a
  generic vendor one. This is consistent with the strategy having been
  written in MultiCharts in 2022.
- **Timestamps: US/Central**, established from the data by
  `run_data_diagnostics` (cash-open volume spike at 08:30, ×6.1 versus the
  preceding half hour; maintenance halt at 16:00–17:00).
- **Coverage: near-continuous Globex**, ~1,098 one-minute bars per session.
- **Contract: front-month continuous**, and consistent with back-adjustment
  — large gaps show no clustering in quarterly roll months (33% observed
  against ~33% expected by chance).
- **Period:** 2015-03 to 2025-04, ~3.4M one-minute rows.
- **Exported:** 3 May 2025 (file creation timestamp), consistent with a
  sample ending in April 2025.

> **Not recorded: the upstream feed.** MultiCharts is the charting
> platform, not the data source, and its export does not name the feed
> that populated it (CQG, IQFeed, Kinetick, Rithmic or another). The
> macOS download-origin attribute on this copy has been lost, so the
> feed is not recoverable from the file. Anyone reproducing these
> results from a different vendor's NQ history should expect small
> differences in the trade list: continuous-contract stitching and
> roll dates are vendor-specific choices, not properties of the market.

This is the one gap in the chain from raw data to published number.
Everything else in this document is verifiable from the file itself, and
`run_data_diagnostics` re-checks the timezone, roll and gap properties on
whatever file is supplied.

## `Data/nq-10y-1min.csv`

Front-month continuous NQ futures, 1-minute bars.

| Column | Type | Notes |
|---|---|---|
| `Date` | string | `%d/%m/%Y` (day first) |
| `Time` | string | `%H:%M:%S` |
| `Open` | float | index points |
| `High` | float | index points |
| `Low` | float | index points |
| `Close` | float | index points |
| `TotalVolume` | int | contracts in the bar |

Properties the code relies on:

- **Timezone: US/Central, no offset applied.** The cash open appears at
  08:30 and the daily maintenance halt at 16:00–17:00. The strategy window
  of 08:30–10:00 is therefore 09:30–11:00 ET, the first 90 minutes of the
  cash session. `run_data_diagnostics` verifies this from the volume
  profile and warns if the file is Eastern-stamped instead.
- **Coverage: near-continuous Globex**, ~1,100 one-minute bars per session.
  The engine walks overnight bars, so a day-session-only file will produce
  different (and more optimistic) results.
- **Continuity: back-adjusted.** Confirmed on another copy of the
  MultiCharts history, where the 2010 series is about 2.2 times the traded
  price, so point differences are right but percentage-of-price thresholds
  are distorted early in the sample. The roll diagnostic reports
  whether large gaps cluster in Mar/Jun/Sep/Dec. Note that a *negative*
  result is only absence of evidence — it does not prove the series is
  adjusted. If it is not, multi-day longs book roll basis as PnL.
- **Bar timestamps: end of bar.** Like every MultiCharts export, the file
  stamps each minute at its end (`08:30` = 08:29–08:30): the first bar
  after the 17:00 reopen is `17:01` on 2,509 of 2,510 sessions. Load it
  with `load_nq_1min(path, stamped_at='end')`. `run_data_diagnostics`
  runs this check on any file. All published results use the end-of-bar
  reading, with one deliberate exception: the first row of the attribution
  chain reads the file as start-stamped, because it reproduces the original
  notebook (1,728 trades, $3,287,392).
- Period behind the published results: **2015-03 to 2025-04**, ~3.4M
  one-minute rows resampling to ~689k five-minute bars (end-of-bar reading).

`load_nq_1min` accepts `.csv` and `.csv.gz`.

## `Data/QQQ_daily.csv`

Daily QQQ closes, used only as a buy & hold benchmark.

| Column | Type | Notes |
|---|---|---|
| `date` | string | any format pandas can parse |
| `close` | float | `Adj Close` is also accepted |

Column names are matched case-insensitively, so a stock vendor export with
`Date` / `Adj Close` works unchanged. The file must span the full backtest
period or `align_benchmark` raises.

## Verifying a file before trusting results

```python
from nq_breakout import load_nq_1min, run_data_diagnostics
run_data_diagnostics(load_nq_1min('Data/nq-10y-1min.csv'))
```

Read that output before any metric. It checks the timezone signature, the
bar timestamp convention, roll splicing, and how often sessions gap more
than the stop distance.
