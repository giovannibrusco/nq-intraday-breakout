"""Phase 11A.2: MAE distribution analysis -> candidate SL values.

Reads `reports/phase11a_mae_sl/trades_baseline_no_sl_no_tp.parquet` (produced by
phase11a_export_trades.py with SL+TP both disabled) and computes percentiles
of MAE distribution to use as candidate SL values for the sweep.

Outputs:
  - reports/phase11a_mae_sl/mae_percentiles.csv
    columns: percentile, sl_points, expected_pct_trades_hitting_sl
  - Console: histogram of MAE bucketed by exit_reason

Rationale: with SL set to MAE percentile P, then (1-P)% of historical trades
had MAE >= SL → would have hit SL (stopped). The remaining P% had MAE < SL →
exit at TP or max_hold. LOWER SL means more frequent stops (loss rate higher
but loss size smaller per stop); HIGHER SL means rarer stops but bigger losses.
Sweep + lifecycle MC will identify the optimum.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="MAE percentile analysis -> candidate SL values for sweep."
    )
    parser.add_argument(
        "--trades",
        default="reports/phase11a_mae_sl/trades_baseline_no_sl_no_tp.parquet",
    )
    parser.add_argument("--out-dir", default="reports/phase11a_mae_sl")
    parser.add_argument(
        "--percentiles",
        nargs="+",
        type=int,
        default=[40, 50, 60, 70, 80, 90],
        help="Percentile values (0-100) to compute as candidate SLs.",
    )
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades = pd.read_parquet(args.trades)
    print(f"[Phase 11A.2] Loaded {len(trades)} trades from {args.trades}")

    if "mae_points" not in trades.columns:
        raise RuntimeError("trades parquet missing 'mae_points' column.")

    mae = trades["mae_points"].dropna()
    print(f"[Phase 11A.2] MAE summary:")
    print(f"  count: {len(mae)}")
    print(f"  min:    {mae.min():.1f}")
    print(f"  q25:    {mae.quantile(0.25):.1f}")
    print(f"  median: {mae.median():.1f}")
    print(f"  q75:    {mae.quantile(0.75):.1f}")
    print(f"  q90:    {mae.quantile(0.90):.1f}")
    print(f"  max:    {mae.max():.1f}")
    print(f"  mean:   {mae.mean():.1f}")
    print(f"  std:    {mae.std():.1f}")

    print("\n[Phase 11A.2] MAE by exit_reason:")
    by_reason = trades.groupby("exit_reason")["mae_points"].agg(["count", "mean", "median", "max"])
    print(by_reason.to_string())

    # Compute candidate SLs from MAE percentiles
    rows = []
    for p in args.percentiles:
        mae_pctile = float(np.percentile(mae, p))
        # Fraction of trades that would hit SL at this level (MAE >= SL)
        pct_hits = float((mae >= mae_pctile).mean()) * 100.0
        rows.append(
            {
                "percentile": p,
                "sl_points": round(mae_pctile, 1),
                "pct_trades_would_hit_sl": round(pct_hits, 1),
            }
        )
    pdf = pd.DataFrame(rows)

    # Add baseline (SL=100) comparison row
    baseline_sl = 100.0
    baseline_pct = float((mae >= baseline_sl).mean()) * 100.0
    pdf = pd.concat(
        [
            pdf,
            pd.DataFrame(
                [{"percentile": "baseline_100", "sl_points": baseline_sl, "pct_trades_would_hit_sl": round(baseline_pct, 1)}]
            ),
        ],
        ignore_index=True,
    )

    out_path = out / "mae_percentiles.csv"
    pdf.to_csv(out_path, index=False)
    print(f"\n[Phase 11A.2] Candidate SL values:")
    print(pdf.to_string(index=False))
    print(f"[Phase 11A.2] Saved -> {out_path}")


if __name__ == "__main__":
    main()
