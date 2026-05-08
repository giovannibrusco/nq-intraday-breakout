"""Trade-level analytics: per-regime breakdown, MAE/MFE distributions.

All binning (gap_atr_bin, range_pctile_bin, vol_ratio_bin) uses quantile-based
cuts computed on the input sample (pd.qcut with 3 equal-frequency bins labelled
low/mid/high). Bin boundaries are therefore sample-specific -- no look-ahead
since this module is used post-hoc for analysis, not as a live filter signal.

Seed 42 is used wherever randomness is required (currently none, but kept as
convention for future callers).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Regime column join
# ---------------------------------------------------------------------------


def add_regime_columns(
    trades: pd.DataFrame,
    daily_features: pd.DataFrame,
) -> pd.DataFrame:
    """Join daily regime features onto trades by entry date.

    Parameters
    ----------
    trades:
        DataFrame produced by run_backtest (must contain an ``entry_time``
        column that can be coerced to a Timestamp).
    daily_features:
        DataFrame produced by filters.compute_daily_features, indexed by
        Python ``datetime.date``.

    Returns
    -------
    DataFrame with the original trades columns plus:
    - ``gap_atr``        -- gap in ATR units (signed)
    - ``gap_atr_bin``    -- 'low' / 'mid' / 'high' (by 33rd/66th quantile)
    - ``range_pctile``   -- prior-day range percentile (0..1)
    - ``range_pctile_bin`` -- 'low' / 'mid' / 'high'
    - ``trend_sign``     -- ema_slope_sign (-1 / 0 / +1)
    - ``vol_ratio``      -- session_vol_ratio
    - ``vol_ratio_bin``  -- 'low' / 'mid' / 'high'
    """
    if trades.empty:
        for col in (
            "gap_atr", "gap_atr_bin", "range_pctile", "range_pctile_bin",
            "trend_sign", "vol_ratio", "vol_ratio_bin",
        ):
            trades = trades.copy()
            trades[col] = pd.NA
        return trades

    df = trades.copy()
    df["_entry_date"] = pd.to_datetime(df["entry_time"]).dt.date

    # Build a lookup frame from daily_features, indexed by date.
    feat = daily_features.copy()
    # Normalise index to Python date for consistent key lookup.
    if not isinstance(feat.index[0], type(df["_entry_date"].iloc[0])):
        feat.index = pd.to_datetime(feat.index).date

    lookup_cols = {
        "gap_atr": "gap_atr",
        "range_pctile": "range_pctile",
        "ema_slope_sign": "trend_sign",
        "session_vol_ratio": "vol_ratio",
    }
    for feat_col, out_col in lookup_cols.items():
        if feat_col in feat.columns:
            df[out_col] = df["_entry_date"].map(feat[feat_col])
        else:
            df[out_col] = np.nan

    df.drop(columns=["_entry_date"], inplace=True)

    # Bin the continuous variables (quantile-based, 3 equal-frequency bins).
    for raw_col, bin_col in [
        ("gap_atr", "gap_atr_bin"),
        ("range_pctile", "range_pctile_bin"),
        ("vol_ratio", "vol_ratio_bin"),
    ]:
        valid = df[raw_col].dropna()
        if len(valid) >= 3 and valid.nunique() >= 3:
            df[bin_col] = pd.qcut(
                df[raw_col],
                q=3,
                labels=["low", "mid", "high"],
                duplicates="drop",
            )
        else:
            df[bin_col] = "mid"

    return df


# ---------------------------------------------------------------------------
# Per-regime breakdown
# ---------------------------------------------------------------------------


def per_regime_breakdown(
    trades: pd.DataFrame,
    regime_col: str,
) -> pd.DataFrame:
    """Per regime bin: count, mean_pnl, median_pnl, win_rate, per-trade sharpe.

    Parameters
    ----------
    trades:
        Trades DataFrame (must contain ``pnl`` column).
    regime_col:
        Column to group by (e.g. 'gap_atr_bin', 'trend_sign', 'exit_reason').

    Returns
    -------
    DataFrame indexed by regime value with columns:
    count, total_pnl, mean_pnl, median_pnl, win_rate, sharpe_per_trade.
    """
    if trades.empty or regime_col not in trades.columns:
        return pd.DataFrame(
            columns=["count", "total_pnl", "mean_pnl", "median_pnl", "win_rate", "sharpe_per_trade"]
        )

    rows = []
    for regime_val, grp in trades.groupby(regime_col, observed=True, dropna=False):
        pnl = grp["pnl"].dropna()
        n = len(pnl)
        total = float(pnl.sum())
        mean = float(pnl.mean()) if n > 0 else float("nan")
        median = float(pnl.median()) if n > 0 else float("nan")
        wr = float((pnl > 0).mean()) if n > 0 else float("nan")

        # Per-trade Sharpe: mean / std * sqrt(n) -- no annualization for trade-level
        if n >= 2:
            std = float(pnl.std(ddof=1))
            spt = float(pnl.mean() / std) if std > 0 else float("nan")
        else:
            spt = float("nan")

        rows.append({
            "regime": regime_val,
            "count": n,
            "total_pnl": total,
            "mean_pnl": mean,
            "median_pnl": median,
            "win_rate": wr,
            "sharpe_per_trade": spt,
        })

    result = pd.DataFrame(rows).set_index("regime")
    return result


# ---------------------------------------------------------------------------
# MAE/MFE distributions
# ---------------------------------------------------------------------------


def mae_mfe_distribution(trades: pd.DataFrame) -> dict:
    """Return MAE/MFE percentile summaries and directional breakdown.

    Parameters
    ----------
    trades:
        DataFrame with ``mae_points``, ``mfe_points``, ``direction`` columns.

    Returns
    -------
    Dict with keys:
    - ``mae_pct``       : {10, 25, 50, 75, 90} percentiles of mae_points
    - ``mfe_pct``       : {10, 25, 50, 75, 90} percentiles of mfe_points
    - ``mae_mfe_ratio`` : per-trade mae / mfe (median of ratio)
    - ``by_direction``  : nested dict {long: {mae_median, mfe_median}, short: ...}
    """
    pct_levels = [10, 25, 50, 75, 90]
    result: dict = {}

    mae = trades["mae_points"].dropna() if "mae_points" in trades.columns else pd.Series(dtype=float)
    mfe = trades["mfe_points"].dropna() if "mfe_points" in trades.columns else pd.Series(dtype=float)

    result["mae_pct"] = {
        p: float(np.percentile(mae, p)) if len(mae) > 0 else float("nan")
        for p in pct_levels
    }
    result["mfe_pct"] = {
        p: float(np.percentile(mfe, p)) if len(mfe) > 0 else float("nan")
        for p in pct_levels
    }

    # MAE/MFE ratio: where mfe > 0, compute per-trade ratio
    if len(mae) > 0 and len(mfe) > 0 and len(mae) == len(mfe):
        valid_mask = mfe > 0
        ratio = (mae[valid_mask] / mfe[valid_mask]).dropna()
        result["mae_mfe_ratio_median"] = float(ratio.median()) if len(ratio) > 0 else float("nan")
    else:
        result["mae_mfe_ratio_median"] = float("nan")

    # Per-direction breakdown
    by_dir: dict = {}
    if "direction" in trades.columns:
        for direction in ["long", "short"]:
            sub = trades[trades["direction"] == direction]
            if sub.empty:
                by_dir[direction] = {
                    "mae_median": float("nan"),
                    "mfe_median": float("nan"),
                    "count": 0,
                }
            else:
                by_dir[direction] = {
                    "mae_median": float(sub["mae_points"].median()) if "mae_points" in sub.columns else float("nan"),
                    "mfe_median": float(sub["mfe_points"].median()) if "mfe_points" in sub.columns else float("nan"),
                    "count": len(sub),
                }
    result["by_direction"] = by_dir
    return result


# ---------------------------------------------------------------------------
# Direction edge breakdown
# ---------------------------------------------------------------------------


def edge_per_direction(trades: pd.DataFrame) -> pd.DataFrame:
    """Long vs short: count, total_pnl, mean_pnl, win_rate, max_dd per direction.

    Parameters
    ----------
    trades:
        Trades DataFrame with ``pnl`` and ``direction`` columns.

    Returns
    -------
    DataFrame indexed by direction ('long' / 'short') with columns:
    count, total_pnl, mean_pnl, win_rate, max_dd.
    """
    if trades.empty or "direction" not in trades.columns:
        return pd.DataFrame(
            columns=["count", "total_pnl", "mean_pnl", "win_rate", "max_dd"]
        )

    rows = []
    for direction in ["long", "short"]:
        sub = trades[trades["direction"] == direction].copy()
        pnl = sub["pnl"].dropna() if not sub.empty else pd.Series(dtype=float)
        n = len(pnl)
        if n == 0:
            rows.append({
                "direction": direction,
                "count": 0,
                "total_pnl": 0.0,
                "mean_pnl": float("nan"),
                "win_rate": float("nan"),
                "max_dd": float("nan"),
            })
            continue

        # Intra-direction max drawdown on cumulative PnL sequence
        cumulative = pnl.cumsum().values
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_dd = float(np.max(drawdowns))

        rows.append({
            "direction": direction,
            "count": n,
            "total_pnl": float(pnl.sum()),
            "mean_pnl": float(pnl.mean()),
            "win_rate": float((pnl > 0).mean()),
            "max_dd": max_dd,
        })

    return pd.DataFrame(rows).set_index("direction")


# ---------------------------------------------------------------------------
# Exit reason breakdown
# ---------------------------------------------------------------------------


def exit_reason_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    """Per exit_reason: count, total_pnl, mean_pnl, mean_mae, mean_mfe.

    Parameters
    ----------
    trades:
        Trades DataFrame with ``exit_reason``, ``pnl``, ``mae_points``,
        ``mfe_points`` columns.

    Returns
    -------
    DataFrame indexed by exit_reason with columns:
    count, total_pnl, mean_pnl, mean_mae, mean_mfe.
    """
    if trades.empty or "exit_reason" not in trades.columns:
        return pd.DataFrame(
            columns=["count", "total_pnl", "mean_pnl", "mean_mae", "mean_mfe"]
        )

    rows = []
    for reason, grp in trades.groupby("exit_reason", observed=True, dropna=False):
        pnl = grp["pnl"].dropna()
        mae = grp["mae_points"].dropna() if "mae_points" in grp.columns else pd.Series(dtype=float)
        mfe = grp["mfe_points"].dropna() if "mfe_points" in grp.columns else pd.Series(dtype=float)

        rows.append({
            "exit_reason": reason,
            "count": len(pnl),
            "total_pnl": float(pnl.sum()),
            "mean_pnl": float(pnl.mean()) if len(pnl) > 0 else float("nan"),
            "mean_mae": float(mae.mean()) if len(mae) > 0 else float("nan"),
            "mean_mfe": float(mfe.mean()) if len(mfe) > 0 else float("nan"),
        })

    return pd.DataFrame(rows).set_index("exit_reason")
