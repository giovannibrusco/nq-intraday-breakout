"""Phase 4 filter-edge analysis.

One-shot script. From the repo root::

    python scripts/phase4_filter_edge.py

Workflow:
  1. Load NQ 1-min parquet (build it via ``scripts/convert_to_parquet.py``
     first; if the parquet is missing we fall back to the CSV via the same
     columns as the legacy loader, slower but works).
  2. Resample to 5-min.
  3. Load QQQ daily parquet (same fallback to CSV).
  4. Compute daily features (gap_atr, range_pctile, ema_slope_sign,
     session_vol_ratio) with strict .shift(1) discipline.
  5. Run baseline backtest (no filters).
  6. Run one backtest per filter (toggle only that filter on).
  7. Run one combined backtest (all 4 filters on).
  8. Compute per-run: trade count, total PnL, Sharpe (daily PnL), per-trade PnL.
  9. Block-bootstrap p-value on Sharpe differential vs baseline (1000 resamples,
     10-day blocks, seed 42).
 10. Write ``reports/phase4_filter_edge.csv`` and
     ``reports/phase4_filter_edge_equity.png``.
 11. Print a summary table with retain/drop verdicts.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nqbreakout.data.loader import load_nq_1min, load_qqq_daily, resample_to_5min
from nqbreakout.strategy.config import FilterSpec, StrategyConfig, load_instrument_spec
from nqbreakout.strategy.core import BacktestResult, run_backtest
from nqbreakout.strategy.filters import FilterEngine, compute_daily_features


# ---------------------------------------------------------------------------
# I/O helpers with CSV fallback
# ---------------------------------------------------------------------------


def _load_nq_with_fallback(parquet_path: Path, csv_path: Path) -> pd.DataFrame:
    """Load NQ 1-min from parquet; fall back to CSV with the same schema."""
    if parquet_path.exists():
        return load_nq_1min(parquet_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Neither NQ parquet ({parquet_path}) nor CSV ({csv_path}) found. "
            "Run `python scripts/convert_to_parquet.py` or place the CSV at Data/nq-10y-1min.csv."
        )
    print(f"[INFO] Parquet missing; loading from {csv_path} (slower)...")
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(
        df["Date"].astype(str) + " " + df["Time"].astype(str),
        format="%d/%m/%Y %H:%M:%S",
    )
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "TotalVolume": "totalvolume",
        }
    )
    df = df[["date", "open", "high", "low", "close", "totalvolume"]]
    return df.sort_values("date").reset_index(drop=True)


def _load_qqq_with_fallback(parquet_path: Path, csv_path: Path) -> pd.DataFrame:
    """Load QQQ daily from parquet; fall back to CSV."""
    if parquet_path.exists():
        return load_qqq_daily(parquet_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Neither QQQ parquet ({parquet_path}) nor CSV ({csv_path}) found."
        )
    print(f"[INFO] QQQ parquet missing; loading from {csv_path}...")
    df = pd.read_csv(csv_path, header=0, skiprows=[1])
    df.columns = [c.strip().lower() for c in df.columns]
    df = df[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna().sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Ablation runner
# ---------------------------------------------------------------------------


FILTER_NAMES = ("overnight_gap", "prior_range", "trend", "liquidity")

FILTER_FIELD = {
    "overnight_gap": "enable_overnight_gap",
    "prior_range": "enable_prior_range",
    "trend": "enable_trend",
    "liquidity": "enable_liquidity",
}


def _spec_with_only(base: FilterSpec, name: str | None) -> FilterSpec:
    """Return a FilterSpec with only ``name`` enabled (or none if name=None)."""
    kwargs = {f: False for f in FILTER_FIELD.values()}
    if name is not None:
        kwargs[FILTER_FIELD[name]] = True
    return replace(base, **kwargs)


def _spec_with_all(base: FilterSpec) -> FilterSpec:
    """Return a FilterSpec with all 4 filters enabled."""
    return replace(
        base,
        enable_overnight_gap=True,
        enable_prior_range=True,
        enable_trend=True,
        enable_liquidity=True,
    )


def run_filter_ablation(
    df_5min: pd.DataFrame,
    daily_features: pd.DataFrame,
    config_base: StrategyConfig,
    instrument,
) -> dict[str, BacktestResult]:
    """Run baseline + 4 individual + 1 combined. Returns {name: BacktestResult}."""
    results: dict[str, BacktestResult] = {}

    # Baseline: no filters enabled.
    cfg_base = replace(config_base, filter_spec=_spec_with_only(config_base.filter_spec, None))
    results["baseline"] = run_backtest(
        df_5min,
        cfg_base,
        instrument,
        filters=FilterEngine(cfg_base.filter_spec, daily_features),
    )
    print(f"  baseline: trades={len(results['baseline'].trades)}")

    for name in FILTER_NAMES:
        spec = _spec_with_only(config_base.filter_spec, name)
        cfg = replace(config_base, filter_spec=spec)
        results[name] = run_backtest(
            df_5min,
            cfg,
            instrument,
            filters=FilterEngine(spec, daily_features),
        )
        print(f"  {name}: trades={len(results[name].trades)}")

    # Combined.
    spec_all = _spec_with_all(config_base.filter_spec)
    cfg_all = replace(config_base, filter_spec=spec_all)
    results["combined"] = run_backtest(
        df_5min,
        cfg_all,
        instrument,
        filters=FilterEngine(spec_all, daily_features),
    )
    print(f"  combined: trades={len(results['combined'].trades)}")
    return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _daily_pnl_series(result: BacktestResult, all_days: pd.DatetimeIndex) -> pd.Series:
    """Project per-trade PnL onto a common daily index, 0 on days without trades.

    Indexed by exit-date (trade-completion day). Aligning to a common index
    across runs is required for the matched-day bootstrap.
    """
    trades = result.trades
    if trades.empty:
        return pd.Series(0.0, index=all_days, name="pnl")
    s = trades.copy()
    s["exit_date"] = pd.to_datetime(s["exit_time"]).dt.normalize()
    daily = s.groupby("exit_date")["pnl"].sum()
    daily = daily.reindex(all_days, fill_value=0.0)
    daily.name = "pnl"
    return daily


def _sharpe(daily: np.ndarray) -> float:
    """Annualized Sharpe on a daily PnL series (252 trading days)."""
    d = np.asarray(daily, dtype=float)
    if d.size < 2:
        return 0.0
    std = d.std(ddof=1)
    if std == 0 or not np.isfinite(std):
        return 0.0
    return float(d.mean() / std * np.sqrt(252.0))


def significance_test(
    baseline_pnl_daily: pd.Series,
    filtered_pnl_daily: pd.Series,
    n_boot: int = 1000,
    block: int = 10,
    seed: int = 42,
) -> tuple[float, float]:
    """Block bootstrap on the Sharpe DIFFERENTIAL (filtered - baseline).

    We sample the same block of day-indices from both series (paired
    bootstrap), compute the Sharpe on each resample, take the difference.
    The p-value is the one-sided tail probability under H0: delta == 0.

    Returns ``(sharpe_delta_observed, p_value)``.
    """
    rng = np.random.default_rng(seed)
    b = baseline_pnl_daily.values.astype(float)
    f = filtered_pnl_daily.values.astype(float)
    assert b.shape == f.shape, "baseline and filtered must share daily index"

    observed = _sharpe(f) - _sharpe(b)
    n = b.shape[0]
    if n < block:
        return float(observed), 1.0

    n_blocks = int(np.ceil(n / block))
    deltas = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        # Build aligned indices for both series.
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        b_samp = b[idx]
        f_samp = f[idx]
        deltas[i] = _sharpe(f_samp) - _sharpe(b_samp)

    # Two-sided p-value against H0: delta == 0.
    # p = fraction of resamples whose |delta| >= |observed|, under the
    # null we center the bootstrap distribution at 0 (the standard trick for
    # paired bootstrap is to recenter).
    centered = deltas - deltas.mean()
    p = float((np.abs(centered) >= abs(observed)).mean())
    return float(observed), p


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class FilterRow:
    name: str
    n_trades: int
    total_pnl: float
    pnl_per_trade: float
    sharpe: float
    sharpe_delta: float
    p_value: float
    verdict: str


def build_results_table(
    results: dict[str, BacktestResult],
    common_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    baseline = results["baseline"]
    baseline_daily = _daily_pnl_series(baseline, common_days)
    baseline_sharpe = _sharpe(baseline_daily.values)

    rows: list[FilterRow] = []
    rows.append(
        FilterRow(
            name="baseline",
            n_trades=int(len(baseline.trades)),
            total_pnl=float(baseline.trades["pnl"].sum()) if not baseline.trades.empty else 0.0,
            pnl_per_trade=(
                float(baseline.trades["pnl"].mean())
                if not baseline.trades.empty
                else 0.0
            ),
            sharpe=baseline_sharpe,
            sharpe_delta=0.0,
            p_value=float("nan"),
            verdict="baseline",
        )
    )

    for name in (*FILTER_NAMES, "combined"):
        r = results[name]
        daily = _daily_pnl_series(r, common_days)
        sharpe = _sharpe(daily.values)
        delta, p = significance_test(baseline_daily, daily)
        if name == "combined":
            verdict = "info"
        else:
            if p < 0.10 and delta > 0:
                verdict = "retain"
            else:
                verdict = f"drop (p={p:.3f}, delta={delta:+.3f})"
        rows.append(
            FilterRow(
                name=name,
                n_trades=int(len(r.trades)),
                total_pnl=float(r.trades["pnl"].sum()) if not r.trades.empty else 0.0,
                pnl_per_trade=(
                    float(r.trades["pnl"].mean()) if not r.trades.empty else 0.0
                ),
                sharpe=sharpe,
                sharpe_delta=delta,
                p_value=p,
                verdict=verdict,
            )
        )

    df = pd.DataFrame([r.__dict__ for r in rows])
    return df


def plot_equity_curves(
    results: dict[str, BacktestResult],
    common_days: pd.DatetimeIndex,
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = {
        "baseline": "#404040",
        "overnight_gap": "#1f77b4",
        "prior_range": "#2ca02c",
        "trend": "#d62728",
        "liquidity": "#9467bd",
        "combined": "#ff7f0e",
    }
    for name, result in results.items():
        daily = _daily_pnl_series(result, common_days)
        equity = daily.cumsum()
        lw = 2.0 if name in ("baseline", "combined") else 1.3
        ax.plot(equity.index, equity.values, label=name, color=colors.get(name), linewidth=lw)
    ax.set_title("Phase 4 filter ablation: cumulative PnL (USD)")
    ax.set_xlabel("date")
    ax.set_ylabel("cumulative PnL")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nq-parquet", type=Path, default=REPO_ROOT / "data" / "nq_1min.parquet")
    parser.add_argument("--nq-csv", type=Path, default=REPO_ROOT / "Data" / "nq-10y-1min.csv")
    parser.add_argument("--qqq-parquet", type=Path, default=REPO_ROOT / "data" / "qqq_daily.parquet")
    parser.add_argument("--qqq-csv", type=Path, default=REPO_ROOT / "Data" / "QQQ_daily.csv")
    parser.add_argument("--out-csv", type=Path, default=REPO_ROOT / "reports" / "phase4_filter_edge.csv")
    parser.add_argument(
        "--out-png",
        type=Path,
        default=REPO_ROOT / "reports" / "phase4_filter_edge_equity.png",
    )
    parser.add_argument("--trend-source", default="NQ", choices=["NQ", "QQQ"])
    args = parser.parse_args()

    print("[1/5] Loading NQ 1-min...")
    df_1min = _load_nq_with_fallback(args.nq_parquet, args.nq_csv)
    print(f"    rows={len(df_1min):,}")

    print("[2/5] Resampling to 5-min...")
    df_5min = resample_to_5min(df_1min)
    print(f"    rows={len(df_5min):,}")

    print("[3/5] Loading QQQ daily and computing daily features...")
    qqq_daily = _load_qqq_with_fallback(args.qqq_parquet, args.qqq_csv)
    base_spec = FilterSpec()
    daily_features = compute_daily_features(
        df_5min,
        qqq_daily=qqq_daily,
        atr_window=base_spec.atr_window,
        range_window_days=base_spec.range_window_days,
        trend_ema_window=base_spec.trend_ema_window,
        trend_source=args.trend_source,
        liquidity_session_start=base_spec.liquidity_session_start,
        liquidity_session_end=base_spec.liquidity_session_end,
        liquidity_median_window_days=base_spec.liquidity_median_window_days,
    )
    print(f"    feature days={len(daily_features)}, "
          f"finite gap_atr={daily_features['gap_atr'].notna().sum()}, "
          f"finite range_pctile={daily_features['range_pctile'].notna().sum()}, "
          f"finite ema_slope={daily_features['ema_slope_sign'].notna().sum()}")

    print("[4/5] Running ablation backtests...")
    cfg = StrategyConfig()
    instrument = load_instrument_spec("NQ")
    results = run_filter_ablation(df_5min, daily_features, cfg, instrument)

    print("[5/5] Building results table and plots...")
    # Common daily index for matched-day bootstrap.
    common_days = pd.DatetimeIndex(
        sorted(pd.Series([d for d in df_5min["date"].dt.normalize().unique()]))
    )
    table = build_results_table(results, common_days)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out_csv, index=False, float_format="%.6f")
    plot_equity_curves(results, common_days, args.out_png)

    print()
    print("=" * 90)
    print("PHASE 4 FILTER EDGE ANALYSIS")
    print("=" * 90)
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(table.to_string(index=False, float_format=lambda v: f"{v: .4f}"))
    print()
    print(f"CSV written: {args.out_csv}")
    print(f"PNG written: {args.out_png}")

    # Print retained filters.
    retained = table[table["verdict"] == "retain"]["name"].tolist()
    print()
    print(f"Retained filters (p<0.10 AND sharpe_delta>0): {retained or 'NONE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
