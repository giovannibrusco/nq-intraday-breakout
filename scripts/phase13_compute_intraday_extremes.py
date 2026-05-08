"""Phase 13.3: Compute per-day intraday balance extremes from trades + 5min bars.

For each Phase 11E aggressive setup (8 products), re-runs backtest and for each
trade computes MAE (Max Adverse Excursion) and MFE (Max Favorable Excursion)
in dollars from the 5-min bars between entry_time and exit_time.

Then per day, aggregates trades sequentially to compute:
  - daily_pnl              = sum of trade PnLs (close to close)
  - intraday_low_delta     = lowest cumulative PnL reached during the day
                              (most negative, ≤ 0)
  - intraday_high_delta    = highest cumulative PnL reached during the day
                              (most positive, ≥ 0)

Output:
  reports/phase13_mae_intraday/intraday_extremes_{product}.parquet  x 8
    columns: ["daily_pnl", "intraday_low_delta", "intraday_high_delta"]
    indexed by trading date

  reports/phase13_mae_intraday/mae_summary.csv
    aggregated per-product stats (avg MAE, max single-day intraday_low, etc.)
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET  # noqa: E402

NQ_MARGIN = 23282.57
MNQ_MARGIN = 100.0
APEX_4_TIER_NAMES = ["apex_25k", "apex_50k", "apex_100k", "apex_150k"]

TIER_MAP = {
    "apex_25k_eod":      ("25K",  "apex_25k",  "eod"),
    "apex_25k_intraday": ("25K",  "apex_25k",  "intraday"),
    "apex_50k_eod":      ("50K",  "apex_50k",  "eod"),
    "apex_50k_intraday": ("50K",  "apex_50k",  "intraday"),
    "apex_100k_eod":     ("100K", "apex_100k", "eod"),
    "apex_100k_intraday":("100K", "apex_100k", "intraday"),
    "apex_150k_eod":     ("150K", "apex_150k", "eod"),
    "apex_150k_intraday":("150K", "apex_150k", "intraday"),
}


def choose_instrument(tier, nq_margin, mnq_margin):
    required_nq = nq_margin * 1.20
    if tier.starting_balance >= required_nq:
        return "NQ", tier.max_contracts_nq, nq_margin
    return "MNQ", tier.max_contracts_mnq, mnq_margin


def aggregate_day_extremes(day_trades: pd.DataFrame, point_value: float) -> tuple[float, float, float]:
    """For one day's trades (sorted by entry_time), compute:
        daily_pnl, intraday_low_delta (≤0), intraday_high_delta (≥0).

    Sequential balance evolution:
        cumulative = 0
        for each trade:
            during trade: balance evolves from cumulative to cumulative + (-mae) or +mfe
            intraday_low_so_far = min(intraday_low_so_far, cumulative - mae)
            intraday_high_so_far = max(intraday_high_so_far, cumulative + mfe)
            after trade: cumulative += pnl
    """
    if day_trades.empty:
        return 0.0, 0.0, 0.0

    cumulative = 0.0
    intraday_low = 0.0
    intraday_high = 0.0
    sorted_trades = day_trades.sort_values("entry_time")
    for _, trade in sorted_trades.iterrows():
        contracts = float(trade["contracts"])
        # Backtest engine already computed mae_points and mfe_points (positive magnitudes).
        mae_pts = float(trade.get("mae_points", 0.0) or 0.0)
        mfe_pts = float(trade.get("mfe_points", 0.0) or 0.0)
        mae_usd = mae_pts * point_value * contracts
        mfe_usd = mfe_pts * point_value * contracts
        # During trade: balance fluctuates between cumulative-mae and cumulative+mfe
        intraday_low = min(intraday_low, cumulative - mae_usd)
        intraday_high = max(intraday_high, cumulative + mfe_usd)
        # Trade closes
        cumulative += float(trade["pnl"])
        # End-of-trade balance also a candidate for intraday extremes
        intraday_low = min(intraday_low, cumulative)
        intraday_high = max(intraday_high, cumulative)
    daily_pnl = cumulative
    return daily_pnl, intraday_low, intraday_high


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--elbow-summary",
                        default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase13_mae_intraday")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 13.3] Loading 5min data...")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts_by_name = {
        t.name: t for t in load_apex_accounts(args.accounts)
        if t.name in APEX_4_TIER_NAMES
    }
    elbow = pd.read_csv(args.elbow_summary)

    summary_rows = []
    for _, e in elbow.iterrows():
        product_name = e["product"]
        tp = int(e["best_tp_global"])
        sl = int(e["best_sl_global"])

        if product_name not in TIER_MAP:
            continue
        tier_short, tier_acc_name, variant = TIER_MAP[product_name]
        tier = accounts_by_name[tier_acc_name]
        inst_name, max_contracts, margin = choose_instrument(tier, NQ_MARGIN, MNQ_MARGIN)
        instr = load_instrument_spec(inst_name)

        cfg = replace(
            base_config,
            target_points=float(tp),
            stop_points=float(sl),
            initial_capital=tier.starting_balance,
            max_position=max_contracts,
            margin_per_contract=margin,
            flatten_at_eod=True,
            eod_cutoff_time=tier.flatten_cutoff,
            long_only=True,
        )

        print(f"\n[Phase 13.3] {product_name}: TP={tp} SL={sl} {inst_name}")
        res = run_backtest(df_5min, cfg, instr)
        trades = res.trades.copy()
        if trades.empty:
            print(f"  no trades; skip")
            continue
        trades["exit_date"] = pd.to_datetime(trades["exit_time"]).dt.normalize()
        trades["entry_date"] = pd.to_datetime(trades["entry_time"]).dt.normalize()

        # Per-day aggregation of intraday extremes
        rows = []
        days = trades["exit_date"].unique()
        days_sorted = sorted(days)
        for day in days_sorted:
            day_trades = trades[trades["exit_date"] == day]
            daily_pnl, intra_low, intra_high = aggregate_day_extremes(
                day_trades, instr.point_value
            )
            rows.append({
                "date": day,
                "n_trades": len(day_trades),
                "daily_pnl": daily_pnl,
                "intraday_low_delta": intra_low,
                "intraday_high_delta": intra_high,
            })
        ext_df = pd.DataFrame(rows).set_index("date")

        # Apply NQ-equivalent factor (same as in lifecycle MC)
        factor = TIER_TO_PARQUET[tier_short][1]
        ext_df_mc = ext_df.copy()
        ext_df_mc["daily_pnl"] *= factor
        ext_df_mc["intraday_low_delta"] *= factor
        ext_df_mc["intraday_high_delta"] *= factor

        out_path = out / f"intraday_extremes_{product_name}.parquet"
        ext_df_mc.to_parquet(out_path)

        # Stats
        n_days = len(ext_df)
        avg_low = float(ext_df["intraday_low_delta"].mean())
        worst_low = float(ext_df["intraday_low_delta"].min())
        avg_high = float(ext_df["intraday_high_delta"].mean())
        n_low_lt_pnl = int((ext_df["intraday_low_delta"] < ext_df["daily_pnl"]).sum())
        diff_low_vs_pnl_pct = 100.0 * n_low_lt_pnl / n_days if n_days else 0.0
        avg_diff_low_minus_minpnl = float(
            (ext_df["intraday_low_delta"] - np.minimum(ext_df["daily_pnl"], 0)).mean()
        )

        summary_rows.append({
            "product": product_name,
            "tier": tier_short,
            "variant": variant,
            "tp": tp, "sl": sl,
            "instrument": inst_name,
            "n_days": n_days,
            "avg_intraday_low_$": round(avg_low, 0),
            "worst_intraday_low_$": round(worst_low, 0),
            "avg_intraday_high_$": round(avg_high, 0),
            "pct_days_intraday_low_below_pnl": round(diff_low_vs_pnl_pct, 1),
            "avg_proxy_underestimate_$": round(avg_diff_low_minus_minpnl, 0),
        })

        print(
            f"  n_days={n_days}  avg_intraday_low=${avg_low:,.0f}  "
            f"worst_intraday_low=${worst_low:,.0f}"
        )
        print(
            f"  pct_days where intraday_low < daily_pnl: {diff_low_vs_pnl_pct:.1f}%"
            f"  (proxy underestimates by avg ${avg_diff_low_minus_minpnl:,.0f})"
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_path = out / "mae_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\n[Phase 13.3] Summary -> {summary_path}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
