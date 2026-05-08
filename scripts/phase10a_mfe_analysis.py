"""Phase 10A.2: MFE distribution analysis -> candidate TP values.

Reads `reports/phase10a_mfe_tp/trades_baseline_no_tp.parquet` (produced by
phase10a_export_trades.py with TP effectively disabled), computes percentiles
of the MFE distribution to use as candidate TP values for the sweep.

Outputs:
  - reports/phase10a_mfe_tp/mfe_percentiles.csv
    columns: percentile, mfe_points, expected_pct_trades_hitting_tp
  - Console: histogram of MFE bucketed by exit_reason

Rationale: with TP set to MFE percentile P, then (1-P)% of historical trades
had MFE >= TP -> would have hit TP (winner). The remaining P% had MFE < TP ->
exit at SL or max_hold. A LOWER TP means more frequent fills (winner rate
higher) but smaller per-win payout; a HIGHER TP means rarer fills but bigger
per-win payouts. Sweep + lifecycle MC will identify the optimum.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(
        description="MFE percentile analysis -> candidate TP values for sweep."
    )
    parser.add_argument(
        "--trades",
        default="reports/phase10a_mfe_tp/trades_baseline_no_tp.parquet",
    )
    parser.add_argument("--out-dir", default="reports/phase10a_mfe_tp")
    parser.add_argument(
        "--percentiles",
        nargs="+",
        type=int,
        default=[40, 50, 60, 70, 80, 90],
        help="Percentile values (0-100) to compute as candidate TPs.",
    )
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades = pd.read_parquet(args.trades)
    print(f"[Phase 10A.2] Loaded {len(trades)} trades from {args.trades}")

    if "mfe_points" not in trades.columns:
        raise RuntimeError("trades parquet missing 'mfe_points' column.")

    mfe = trades["mfe_points"].dropna()
    print(f"[Phase 10A.2] MFE summary:")
    print(f"  count: {len(mfe)}")
    print(f"  min:    {mfe.min():.1f}")
    print(f"  q25:    {mfe.quantile(0.25):.1f}")
    print(f"  median: {mfe.median():.1f}")
    print(f"  q75:    {mfe.quantile(0.75):.1f}")
    print(f"  q90:    {mfe.quantile(0.90):.1f}")
    print(f"  max:    {mfe.max():.1f}")
    print(f"  mean:   {mfe.mean():.1f}")
    print(f"  std:    {mfe.std():.1f}")

    # Per-exit-reason MFE
    print("\n[Phase 10A.2] MFE by exit_reason:")
    by_reason = trades.groupby("exit_reason")["mfe_points"].agg(["count", "mean", "median", "max"])
    print(by_reason.to_string())

    # Compute candidate TPs from percentiles
    rows = []
    for p in args.percentiles:
        mfe_pctile = float(np.percentile(mfe, p))
        # Fraction of trades that would hit TP at this level (MFE >= TP)
        pct_hits = float((mfe >= mfe_pctile).mean()) * 100.0
        rows.append(
            {
                "percentile": p,
                "tp_points": round(mfe_pctile, 1),
                "pct_trades_would_hit_tp": round(pct_hits, 1),
            }
        )
    pdf = pd.DataFrame(rows)

    # Add baseline (TP=200) comparison row for reference
    baseline_tp = 200.0
    baseline_pct = float((mfe >= baseline_tp).mean()) * 100.0
    pdf = pd.concat(
        [
            pdf,
            pd.DataFrame(
                [{"percentile": "baseline_200", "tp_points": baseline_tp, "pct_trades_would_hit_tp": round(baseline_pct, 1)}]
            ),
        ],
        ignore_index=True,
    )

    out_path = out / "mfe_percentiles.csv"
    pdf.to_csv(out_path, index=False)
    print(f"\n[Phase 10A.2] Candidate TP values:")
    print(pdf.to_string(index=False))
    print(f"[Phase 10A.2] Saved -> {out_path}")


if __name__ == "__main__":
    main()
