# Data

Market data is **not** committed. Place the two CSVs below in a `Data/`
folder at the repository root (capital `D` — that is what the notebook
loads) and they will be picked up. `Data/` is git-ignored.

> **TODO (needs the author's input):** the vendor and download date of both
> files are not recorded anywhere in the repository history. The NQ column
> layout (`Date`, `Time`, ..., `TotalVolume`, with `dd/mm/yyyy` dates) is a
> MultiCharts-style export, which is consistent with the strategy's
> MultiCharts provenance, but the underlying feed (CQG / IQFeed / Kinetick
> / other) cannot be determined from the file alone. Fill this in before
> publishing — results that cannot be traced to a named feed cannot be
> independently checked.

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
