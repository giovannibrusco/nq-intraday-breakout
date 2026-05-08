"""stats.bootstrap: moving-block bootstrap (Kunsch MBB) for confidence intervals.

Clean stats-layer implementation. Does not import from nqbreakout.prop;
same algorithm as prop.montecarlo.block_bootstrap_days but generalized
to return full resample matrices instead of single draws.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .metrics import calmar, max_drawdown_pct, sharpe, sortino


def moving_block_bootstrap(
    x: pd.Series | np.ndarray,
    block_size: int,
    n_resamples: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Kunsch moving-block bootstrap resampler.

    Samples overlapping blocks of length block_size with replacement,
    concatenates, and truncates to len(x). Repeats n_resamples times.

    Returns
    -------
    np.ndarray of shape (n_resamples, len(x)).
    """
    vals = np.asarray(x if isinstance(x, np.ndarray) else x.values, dtype=float)
    n = len(vals)
    if block_size < 1:
        raise ValueError(f"block_size must be >= 1, got {block_size}")
    if n < block_size:
        raise ValueError(f"Series length {n} is shorter than block_size {block_size}")

    n_blocks_per_resample = int(np.ceil(n / block_size))
    # Draw all start indices at once for efficiency.
    all_starts = rng.integers(0, n - block_size + 1, size=(n_resamples, n_blocks_per_resample))

    out = np.empty((n_resamples, n), dtype=float)
    for i in range(n_resamples):
        blocks = [vals[s : s + block_size] for s in all_starts[i]]
        row = np.concatenate(blocks)[:n]
        out[i] = row
    return out


def bootstrap_statistic_ci(
    daily_pnl: pd.Series,
    statistic: Callable[[pd.Series], float],
    block_size: int = 10,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> dict:
    """Bootstrap a scalar statistic over daily PnL and return CI.

    Uses the percentile method (Hall 1992). Preserves autocorrelation
    structure via moving-block bootstrap.

    Returns
    -------
    dict with keys: point, mean, std, ci_lower, ci_upper.
    """
    rng = np.random.default_rng(seed)
    point = statistic(daily_pnl)

    vals = daily_pnl.dropna()
    resamples = moving_block_bootstrap(vals, block_size, n_resamples, rng)

    boot_stats = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        s = pd.Series(resamples[i], dtype=float)
        boot_stats[i] = statistic(s)

    # Filter out nans from degenerate resamples before CI computation.
    valid = boot_stats[~np.isnan(boot_stats)]
    if len(valid) == 0:
        return {
            "point": point,
            "mean": float("nan"),
            "std": float("nan"),
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
        }

    return {
        "point": float(point),
        "mean": float(np.mean(valid)),
        "std": float(np.std(valid, ddof=1)),
        "ci_lower": float(np.percentile(valid, 100 * alpha / 2)),
        "ci_upper": float(np.percentile(valid, 100 * (1 - alpha / 2))),
    }


def bootstrap_all_metrics(
    daily_pnl: pd.Series,
    equity: pd.Series | None = None,
    block_size: int = 10,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> pd.DataFrame:
    """Bootstrap Sharpe, Sortino, Calmar, and MaxDD.

    Returns a DataFrame with columns [metric, point, mean, std, ci_lower,
    ci_upper]. One row per metric.

    If equity is None, Calmar and MaxDD are skipped (they require cumulative
    equity, not just daily PnL).
    """
    # Build running equity from daily_pnl if not provided.
    if equity is None:
        equity = daily_pnl.cumsum()

    base_seed = seed
    rows = []

    # Sharpe
    ci = bootstrap_statistic_ci(daily_pnl, sharpe, block_size, n_resamples, alpha, base_seed)
    rows.append({"metric": "sharpe", **ci})

    # Sortino
    ci = bootstrap_statistic_ci(
        daily_pnl, sortino, block_size, n_resamples, alpha, base_seed + 1
    )
    rows.append({"metric": "sortino", **ci})

    # Calmar: needs equity. Wrap in a closure that reconstructs equity
    # from cumulative pnl relative to the first equity value.
    eq_start = float(equity.iloc[0]) - float(daily_pnl.iloc[0]) if len(equity) > 0 else 0.0

    def calmar_from_pnl(pnl: pd.Series) -> float:
        eq = pd.Series(eq_start + pnl.cumsum().values, dtype=float)
        return calmar(pnl, eq)

    ci = bootstrap_statistic_ci(
        daily_pnl, calmar_from_pnl, block_size, n_resamples, alpha, base_seed + 2
    )
    rows.append({"metric": "calmar", **ci})

    # MaxDD pct: same equity reconstruction approach.
    def max_dd_pct_from_pnl(pnl: pd.Series) -> float:
        eq = pd.Series(eq_start + pnl.cumsum().values, dtype=float)
        return max_drawdown_pct(eq)

    ci = bootstrap_statistic_ci(
        daily_pnl, max_dd_pct_from_pnl, block_size, n_resamples, alpha, base_seed + 3
    )
    rows.append({"metric": "max_dd_pct", **ci})

    return pd.DataFrame(rows)
