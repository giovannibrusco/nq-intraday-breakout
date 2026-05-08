"""Phase 10B.1: Tag each trading day with a vol regime from overnight session.

For each trading day T, compute the GLOBEX overnight ATR proxy:
  - Overnight session = bars with time in [18:00 prev day, 09:30 T) ET
  - Range = max(high) - min(low) of overnight bars (in price points)

Then bucket each day into a regime using a rolling 60-day quantile cut:
  - "low":  overnight_range <= rolling_q33
  - "med":  rolling_q33 < overnight_range <= rolling_q67
  - "high": overnight_range > rolling_q67

Output: reports/phase10b_regime_mc/daily_regime_tags.parquet
        columns: date, overnight_range, regime
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import numpy as np

from nqbreakout.data.loader import load_nq_1min


def compute_overnight_range(df_1min: pd.DataFrame) -> pd.DataFrame:
    """Compute per-trading-day overnight range from 1-min OHLCV.

    Trading day boundary: 09:30 ET. Overnight session for day T = bars with
    timestamp >= (T-1) 18:00 and < T 09:30. Returns DataFrame with columns
    `date` (trading day, normalized) and `overnight_range`.
    """
    df = df_1min.copy()
    df["t"] = df["date"].dt.time
    df["d"] = df["date"].dt.normalize()

    # "Trading day attribution": bars with time >= 18:00 belong to NEXT day's overnight
    # bars with time < 09:30 belong to CURRENT day's overnight
    # bars with time in [09:30, 18:00) are RTH or pre-mkt (not used here)
    overnight_evening = df[df["t"] >= pd.Timestamp("18:00").time()].copy()
    overnight_evening["trading_day"] = overnight_evening["d"] + pd.Timedelta(days=1)
    overnight_morning = df[df["t"] < pd.Timestamp("09:30").time()].copy()
    overnight_morning["trading_day"] = overnight_morning["d"]

    overnight = pd.concat([overnight_evening, overnight_morning], ignore_index=True)
    grouped = overnight.groupby("trading_day").agg(
        overnight_high=("high", "max"),
        overnight_low=("low", "min"),
        n_bars=("date", "count"),
    )
    grouped["overnight_range"] = grouped["overnight_high"] - grouped["overnight_low"]
    grouped = grouped.reset_index().rename(columns={"trading_day": "date"})
    return grouped[["date", "overnight_range", "n_bars"]]


def tag_regimes(overnight_df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Tag each day as low/med/high using rolling quantile of overnight_range.

    First `window` days have no rolling stats -> tagged "med" by default.
    """
    df = overnight_df.copy().sort_values("date").reset_index(drop=True)
    df["q33"] = df["overnight_range"].rolling(window, min_periods=1).quantile(0.33)
    df["q67"] = df["overnight_range"].rolling(window, min_periods=1).quantile(0.67)

    def assign(row):
        if row["overnight_range"] <= row["q33"]:
            return "low"
        if row["overnight_range"] >= row["q67"]:
            return "high"
        return "med"

    df["regime"] = df.apply(assign, axis=1)
    return df[["date", "overnight_range", "n_bars", "regime"]]


def transition_matrix(regimes: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Compute Markov 3x3 transition matrix and stationary distribution."""
    states = ["low", "med", "high"]
    counts = pd.DataFrame(0, index=states, columns=states, dtype=float)
    prev = None
    for r in regimes:
        if prev is not None:
            counts.loc[prev, r] += 1
        prev = r
    matrix = counts.div(counts.sum(axis=1), axis=0).fillna(0.0)

    # Stationary distribution: solve π M = π
    P = matrix.values
    eigvals, eigvecs = np.linalg.eig(P.T)
    idx = np.argmin(abs(eigvals - 1.0))
    stat = np.real(eigvecs[:, idx])
    stat = stat / stat.sum()
    stat = pd.Series(stat, index=states)

    return matrix, stat


def main():
    parser = argparse.ArgumentParser(description="Tag each trading day with vol regime.")
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--out-dir", default="reports/phase10b_regime_mc")
    parser.add_argument("--window", type=int, default=60, help="Rolling window for quantile cuts.")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 10B.1] Loading 1-min data: {args.data}")
    df_1min = load_nq_1min(args.data)
    print(f"[Phase 10B.1] {len(df_1min)} 1-min bars, range {df_1min['date'].min()} -> {df_1min['date'].max()}")

    overnight = compute_overnight_range(df_1min)
    print(
        f"[Phase 10B.1] Overnight range: median={overnight['overnight_range'].median():.1f}pt, "
        f"mean={overnight['overnight_range'].mean():.1f}pt, "
        f"days={len(overnight)}, avg_bars/day={overnight['n_bars'].mean():.0f}"
    )

    tagged = tag_regimes(overnight, window=args.window)
    print(f"\n[Phase 10B.1] Regime distribution:")
    print(tagged["regime"].value_counts(normalize=True).round(3).to_string())

    matrix, stat = transition_matrix(tagged["regime"])
    print(f"\n[Phase 10B.1] Markov transition matrix:")
    print(matrix.round(3).to_string())
    print(f"\n[Phase 10B.1] Stationary distribution:")
    print(stat.round(3).to_string())

    # Diagonal sum = persistence
    persistence = matrix.values.diagonal().mean()
    print(f"\n[Phase 10B.1] Avg regime persistence (diagonal mean): {persistence:.3f}")
    print("  Higher = more sticky regimes; close to 1/3 = no regime structure (random)")

    tags_path = out / "daily_regime_tags.parquet"
    tagged.to_parquet(tags_path)
    matrix.to_csv(out / "transition_matrix.csv")
    stat.to_frame("stationary").to_csv(out / "stationary_distribution.csv")
    print(f"\n[Phase 10B.1] Saved: {tags_path}, transition_matrix.csv, stationary_distribution.csv")


if __name__ == "__main__":
    main()
