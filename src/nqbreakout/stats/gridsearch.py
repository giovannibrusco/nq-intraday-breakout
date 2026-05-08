"""stats.gridsearch: parameter grid search with stability scoring.

Iterates all combinations in a parameter grid, runs backtests, collects
metrics, and scores each combination by both performance and stability
(similarity to neighboring parameter settings).
"""

from __future__ import annotations

import dataclasses
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from nqbreakout.strategy.config import InstrumentSpec, StrategyConfig
from nqbreakout.strategy.core import run_backtest

from .metrics import calmar, max_drawdown_pct, profit_factor, sharpe, sortino, win_rate


def load_grid(path: str | Path) -> dict[str, list]:
    """Load a grid YAML file.

    Expects a top-level 'grid:' key mapping parameter names to lists of values.

    Returns
    -------
    dict[str, list] -- parameter name to list of candidate values.
    """
    data = yaml.safe_load(Path(path).read_text())
    raw = data.get("grid", data)  # fallback: root-level dict
    return {k: list(v) for k, v in raw.items()}


def _metrics_from_result(result) -> dict:
    """Extract all Phase 5 metrics from a BacktestResult."""
    trades_df = result.trades
    n_trades = int(len(trades_df))

    if n_trades == 0 or result.daily_equity.empty:
        return {
            "total_pnl": 0.0,
            "n_trades": 0,
            "sharpe": float("nan"),
            "sortino": float("nan"),
            "calmar": float("nan"),
            "max_dd_pct": float("nan"),
            "profit_factor": float("nan"),
            "win_rate": float("nan"),
        }

    eq = result.daily_equity.sort_index()
    initial = float(result.config.initial_capital)
    eq_values = eq.values.astype(float)
    daily_pnl_vals = []
    prev = initial
    for v in eq_values:
        daily_pnl_vals.append(v - prev)
        prev = v
    daily_pnl = pd.Series(daily_pnl_vals, dtype=float)

    equity_series = pd.Series(eq_values, dtype=float)
    trade_pnl = trades_df["pnl"].astype(float) if "pnl" in trades_df.columns else pd.Series(dtype=float)
    total_pnl = float(trade_pnl.sum()) if len(trade_pnl) > 0 else 0.0

    return {
        "total_pnl": total_pnl,
        "n_trades": n_trades,
        "sharpe": sharpe(daily_pnl),
        "sortino": sortino(daily_pnl),
        "calmar": calmar(daily_pnl, equity_series),
        "max_dd_pct": max_drawdown_pct(equity_series),
        "profit_factor": profit_factor(trade_pnl),
        "win_rate": win_rate(trade_pnl),
    }


def run_grid(
    df_5min: pd.DataFrame,
    base_config: StrategyConfig,
    instrument: InstrumentSpec,
    grid: dict[str, list],
    cost_model=None,
    filters=None,
    n_jobs: int = 1,
) -> pd.DataFrame:
    """Run backtest for every combination in the parameter grid.

    For each combination, calls dataclasses.replace(base_config, **combo)
    to create the per-combination config. Only parameters that are fields
    of StrategyConfig can be swept; others raise a TypeError.

    Parameters
    ----------
    n_jobs:
        Number of parallel workers. n_jobs=1 means sequential (default,
        guarantees reproducibility). n_jobs > 1 uses joblib.Parallel with
        backend='loky'. Note: joblib is available but sequential is preferred
        for deterministic results.

    Returns
    -------
    pd.DataFrame with one row per combination. Columns: all grid parameters
    plus total_pnl, n_trades, sharpe, sortino, calmar, max_dd_pct,
    profit_factor, win_rate.
    """
    param_names = list(grid.keys())
    param_values = [grid[k] for k in param_names]
    combos = list(product(*param_values))

    def _run_one(combo: tuple) -> dict[str, Any]:
        kwargs = dict(zip(param_names, combo))
        cfg = dataclasses.replace(base_config, **kwargs)
        result = run_backtest(df_5min, cfg, instrument, filters=filters, cost_model=cost_model)
        row = dict(kwargs)
        row.update(_metrics_from_result(result))
        return row

    if n_jobs == 1:
        rows = [_run_one(c) for c in combos]
    else:
        try:
            from joblib import Parallel, delayed  # type: ignore[import]
            rows = Parallel(n_jobs=n_jobs, backend="loky")(
                delayed(_run_one)(c) for c in combos
            )
        except ImportError:
            rows = [_run_one(c) for c in combos]

    return pd.DataFrame(rows)


def stability_score(
    grid_results: pd.DataFrame,
    metric: str = "sharpe",
    param: str = "stop_points",
    neighbors: int = 1,
) -> pd.DataFrame:
    """Compute a stability score for each row along one parameter axis.

    For each unique combination of the OTHER parameters (i.e., holding all
    params fixed except `param`), the function looks at the `neighbors`
    adjacent values on the `param` axis and computes the mean metric value
    across the focal row plus its neighbors. A high stability score means
    the combination performs similarly to its neighbors -- less overfit.

    Parameters
    ----------
    grid_results:
        DataFrame returned by run_grid.
    metric:
        Column name to measure stability on.
    param:
        The parameter axis along which to compare neighbors.
    neighbors:
        How many steps in each direction to include (1 = one neighbor on
        each side).

    Returns
    -------
    grid_results with an additional column 'stability_{metric}_{param}'.
    """
    out = grid_results.copy()
    col_name = f"stability_{metric}_{param}"

    if param not in out.columns or metric not in out.columns:
        out[col_name] = float("nan")
        return out

    param_vals = sorted(out[param].unique())
    param_index = {v: i for i, v in enumerate(param_vals)}

    other_params = [c for c in out.columns if c != param and c in _infer_param_cols(out)]

    stability_vals = np.full(len(out), float("nan"))

    for idx, row in out.iterrows():
        p_idx = param_index[row[param]]
        neighbor_indices = range(
            max(0, p_idx - neighbors), min(len(param_vals), p_idx + neighbors + 1)
        )
        neighbor_param_vals = [param_vals[ni] for ni in neighbor_indices]

        # Build mask: same values for all other params, one of the neighbor param values.
        mask = pd.Series([True] * len(out), index=out.index)
        for op in other_params:
            mask &= out[op] == row[op]
        mask &= out[param].isin(neighbor_param_vals)

        neighbor_metrics = out.loc[mask, metric].dropna()
        if len(neighbor_metrics) > 0:
            stability_vals[out.index.get_loc(idx)] = float(neighbor_metrics.mean())

    out[col_name] = stability_vals
    return out


def _infer_param_cols(df: pd.DataFrame) -> list[str]:
    """Return column names that look like grid parameters (non-metric cols)."""
    metric_cols = {
        "total_pnl",
        "n_trades",
        "sharpe",
        "sortino",
        "calmar",
        "max_dd_pct",
        "profit_factor",
        "win_rate",
    }
    # Also exclude any stability columns added by stability_score.
    return [
        c for c in df.columns
        if c not in metric_cols and not c.startswith("stability_") and not c.startswith("score_")
    ]


def pick_robust_config(
    grid_results: pd.DataFrame,
    primary_metric: str = "sharpe",
    stability_weight: float = 0.5,
) -> pd.Series:
    """Select the parameter combination with the best combined score.

    Score = (1 - stability_weight) * z_score(metric) +
              stability_weight      * z_score(stability_col)

    where z_score is (x - mean) / std across all rows. If no stability
    column is found for primary_metric, falls back to selecting the row
    with the highest primary_metric value.

    Returns
    -------
    pd.Series representing the best row in grid_results.
    """
    df = grid_results.copy()

    # Look for a stability column that matches the primary metric.
    stability_col = None
    for col in df.columns:
        if col.startswith(f"stability_{primary_metric}"):
            stability_col = col
            break

    metric_vals = pd.to_numeric(df[primary_metric], errors="coerce")

    # Z-score normalization (handle zero std gracefully).
    def z_score(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        mean = float(valid.mean()) if len(valid) > 0 else 0.0
        std = float(valid.std()) if len(valid) > 1 else 0.0
        if std == 0 or np.isnan(std):
            return s.fillna(0.0) - mean
        return (s - mean) / std

    if stability_col is None or df[stability_col].isna().all():
        # Fallback: pure metric ranking.
        z = z_score(metric_vals)
        best_idx = int(z.fillna(-np.inf).idxmax())
        return df.iloc[df.index.get_loc(best_idx)]

    stab_vals = pd.to_numeric(df[stability_col], errors="coerce")

    combined = (
        (1.0 - stability_weight) * z_score(metric_vals)
        + stability_weight * z_score(stab_vals)
    )
    df["_score"] = combined
    best_iloc = int(combined.fillna(-np.inf).argmax())
    return df.drop(columns=["_score"]).iloc[best_iloc]
