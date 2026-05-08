"""stats.walkforward: rolling walk-forward train/test analysis.

Implements time-series cross-validation using non-overlapping test windows.
Supports an optional optimizer callable for per-fold parameter refit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from nqbreakout.strategy.config import InstrumentSpec, StrategyConfig
from nqbreakout.strategy.core import BacktestResult, run_backtest

from .metrics import sharpe, summary


@dataclass
class WFFold:
    """Date boundaries for one walk-forward fold."""

    fold_id: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class WFResult:
    """Aggregated results of a walk-forward run."""

    folds: list[WFFold]
    train_results: list[BacktestResult]
    test_results: list[BacktestResult]
    summary: pd.DataFrame  # one row per fold with train/test metrics


def generate_folds(
    df_5min: pd.DataFrame,
    train_years: int = 5,
    test_years: int = 1,
    step_years: int = 1,
) -> list[WFFold]:
    """Generate rolling train/test folds from a 5-minute bar DataFrame.

    Each fold's train window is train_years long; test follows immediately.
    Windows slide by step_years. Generation stops when there is not enough
    data for a full test window.

    Parameters
    ----------
    df_5min:
        DataFrame with a 'date' column (Timestamp).
    train_years, test_years, step_years:
        Window sizes in calendar years (approximate; uses pd.DateOffset).
    """
    dates = df_5min["date"]
    global_start = dates.iloc[0].normalize()
    global_end = dates.iloc[-1].normalize()

    train_offset = pd.DateOffset(years=train_years)
    test_offset = pd.DateOffset(years=test_years)
    step_offset = pd.DateOffset(years=step_years)

    folds: list[WFFold] = []
    fold_id = 0
    window_start = global_start

    while True:
        train_start = window_start
        train_end = window_start + train_offset - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + test_offset - pd.Timedelta(days=1)

        # Stop if test window extends beyond available data.
        if test_end > global_end:
            break

        folds.append(
            WFFold(
                fold_id=fold_id,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        fold_id += 1
        window_start = window_start + step_offset

    return folds


def _slice_df(df_5min: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Return rows of df_5min whose 'date' falls in [start, end] (day-level)."""
    mask = (df_5min["date"] >= start) & (df_5min["date"] <= end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
    return df_5min.loc[mask].reset_index(drop=True)


def _fold_metrics(result: BacktestResult) -> dict:
    """Extract summary metrics from a BacktestResult for the WF summary table."""
    trades_df = result.trades
    n_trades = int(len(trades_df))

    if n_trades == 0 or result.daily_equity.empty:
        return {
            "sharpe": float("nan"),
            "total_pnl": 0.0,
            "n_trades": 0,
        }

    # Build daily PnL from equity differences.
    eq = result.daily_equity.sort_index()
    # First day PnL is equity - initial capital
    initial = float(result.config.initial_capital)
    eq_values = eq.values.astype(float)
    daily_pnl_vals = []
    prev = initial
    for v in eq_values:
        daily_pnl_vals.append(v - prev)
        prev = v
    daily_pnl = pd.Series(daily_pnl_vals, dtype=float)

    s = sharpe(daily_pnl)
    total_pnl = float(trades_df["pnl"].sum()) if "pnl" in trades_df.columns else 0.0

    return {
        "sharpe": s,
        "total_pnl": total_pnl,
        "n_trades": n_trades,
    }


def run_walkforward(
    df_5min: pd.DataFrame,
    base_config: StrategyConfig,
    instrument: InstrumentSpec,
    train_years: int = 5,
    test_years: int = 1,
    step_years: int = 1,
    optimizer: Callable | None = None,
    cost_model=None,
    filters=None,
) -> WFResult:
    """Run rolling walk-forward analysis.

    Parameters
    ----------
    df_5min:
        Full 5-minute bar DataFrame.
    base_config:
        Default strategy configuration. Used directly (no refit) when
        optimizer is None; passed as seed to optimizer when provided.
    instrument:
        Instrument spec (NQ / MNQ).
    train_years, test_years, step_years:
        Rolling window sizes.
    optimizer:
        Optional callable with signature
            optimizer(train_df: pd.DataFrame, config: StrategyConfig) -> StrategyConfig.
        When provided, a new config is fitted on the train window and applied
        to the test window. When None, base_config is applied to both windows.
    cost_model:
        Passed through to run_backtest. Uses DEFAULT_COST_MODEL when None.
    filters:
        Passed through to run_backtest. Uses passthrough FilterEngine when None.

    Returns
    -------
    WFResult with per-fold BacktestResults and a summary DataFrame.
    """
    folds = generate_folds(df_5min, train_years, test_years, step_years)
    if not folds:
        raise ValueError(
            f"No folds generated. Data range may be too short for "
            f"train_years={train_years} + test_years={test_years}."
        )

    train_results: list[BacktestResult] = []
    test_results: list[BacktestResult] = []
    summary_rows: list[dict] = []

    for fold in folds:
        train_df = _slice_df(df_5min, fold.train_start, fold.train_end)
        test_df = _slice_df(df_5min, fold.test_start, fold.test_end)

        if len(train_df) == 0 or len(test_df) == 0:
            # Skip degenerate folds (can happen near data boundaries).
            continue

        # Determine the config to use on the test window.
        if optimizer is not None:
            test_config = optimizer(train_df, base_config)
        else:
            test_config = base_config

        # Always run train with base_config (or the optimizer's chosen config)
        # to measure in-sample performance reference.
        train_result = run_backtest(
            train_df,
            test_config,
            instrument,
            filters=filters,
            cost_model=cost_model,
        )
        test_result = run_backtest(
            test_df,
            test_config,
            instrument,
            filters=filters,
            cost_model=cost_model,
        )

        train_results.append(train_result)
        test_results.append(test_result)

        tm = _fold_metrics(train_result)
        om = _fold_metrics(test_result)

        oos_deg = (
            (tm["sharpe"] - om["sharpe"])
            if not (pd.isna(tm["sharpe"]) or pd.isna(om["sharpe"]))
            else float("nan")
        )

        summary_rows.append(
            {
                "fold_id": fold.fold_id,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "train_sharpe": tm["sharpe"],
                "train_pnl": tm["total_pnl"],
                "train_trades": tm["n_trades"],
                "test_sharpe": om["sharpe"],
                "test_pnl": om["total_pnl"],
                "test_trades": om["n_trades"],
                "oos_degradation": oos_deg,
            }
        )

    summary_df = pd.DataFrame(summary_rows)
    return WFResult(
        folds=folds,
        train_results=train_results,
        test_results=test_results,
        summary=summary_df,
    )
