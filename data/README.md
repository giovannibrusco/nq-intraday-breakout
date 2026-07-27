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
  `run_data_diagnostics` (cash-open volume spike at 08:30, ×6.4 versus the
  preceding half hour; maintenance halt at 16:00–17:00).
- **Coverage: near-continuous Globex**, ~1,098 one-minute bars per session.
- **Contract: front-month continuous**, and consistent with back-adjustment
  — large gaps show no clustering in quarterly roll months (33% observed
  against ~33% expected by chance).
- **Period:** 2015-03 to 2025-04, ~3.4M one-minute rows.

> **Open item — the upstream feed.** MultiCharts is the charting platform,
> not the data source; the export does not record which feed populated it
> (CQG, IQFeed, Kinetick, Rithmic or another). That single fact is not
> recoverable from the file and is the one piece missing before an outside
> reader can independently reproduce these results.

On macOS the download origin is often still recorded in the file's
extended attributes, which answers this directly:

```bash
mdls -name kMDItemWhereFroms Data/nq-10y-1min.csv
xattr -p com.apple.metadata:kMDItemWhereFroms Data/nq-10y-1min.csv | xxd -r -p | strings
stat -f '%SB' Data/nq-10y-1min.csv        # file creation date
```

If those come back empty (the attribute is lost when a file is copied
through some tools), the fallback is the MultiCharts installation's data
source settings, or the account the historical data was purchased through.

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
- **Continuity: assumed back-adjusted.** The roll diagnostic reports
  whether large gaps cluster in Mar/Jun/Sep/Dec. Note that a *negative*
  result is only absence of evidence — it does not prove the series is
  adjusted. If it is not, multi-day longs book roll basis as PnL.
- Period behind the published results: **2015-03 to 2025-04**, ~3.4M
  one-minute rows resampling to ~693k five-minute bars.

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

Read that output before any metric. It checks the timezone signature, roll
splicing, and how often sessions gap more than the stop distance.
