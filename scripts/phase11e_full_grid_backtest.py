"""Phase 11E.1: Full grid backtest TP × SL with elbow-finding extension.

Computes all (TP, SL, tier) cells in extended grid, skipping cells already
computed in Phase 11B / 11D. Uses 5-digit SL padding to accommodate SL=10000.

Grid:
  - TP ∈ {40, 60, 82, 115, 168}  (5 values, 40 and 60 NEW)
  - SL ∈ {30, 40, 56, 88, 100, 137, 200, 250, 300, 400,
         500, 600, 800, 1000, 1500, 2000, 10000}  (17 values, 7 NEW wide)
  - Tier ∈ {25K, 50K, 100K, 150K}

Total: 5 × 17 × 4 = 340 cells. Existing ~98. New ~242.

Output: reports/phase11e_sl_elbow/daily_pnl_tp{TP:03d}_sl{SL:05d}/{tier}_longonly.parquet
        reports/phase11e_sl_elbow/full_grid_backtest_summary.csv
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest

NQ_MARGIN = 23282.57
MNQ_MARGIN_ASSUMPTION = 100.0
APEX_4_TIER_NAMES = ["apex_25k", "apex_50k", "apex_100k", "apex_150k"]

# Phase E rev: Apex Daily Loss Limit (live nominal $, PA scaling level 0).
# Backtest at 1 NQ direct → DLL passed as absolute dollar value (no per-contract
# scaling). Modeled as live trader at PA L0 sizing: 1 NQ-eq for 25K, 2 NQ for
# 50K, 3 NQ for 100K, 4 NQ for 150K. Higher scaling tiers handled by MC layer.
TIER_LIVE_DLL = {
    "apex_25k":   500.0,
    "apex_50k":  1000.0,
    "apex_100k": 1750.0,
    "apex_150k": 2500.0,
}

TPS = [40, 60, 82, 115, 168]
SLS = [2, 4, 6, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 40, 56, 88, 100, 137, 200, 250, 300, 400, 500, 600, 800, 1000, 1500, 2000, 10000]

# Existing dirs (use 3-digit SL padding)
EXISTING_DIRS = [
    Path("reports/phase11b_grid_tp_sl"),
    Path("reports/phase11d_extended_sl"),
]


def cell_exists_anywhere(tp: int, sl: int, tier: str) -> Path | None:
    """Check 11B and 11D dirs for existing parquet (3-digit SL)."""
    for base in EXISTING_DIRS:
        # 3-digit padding (SL <= 400 in 11B/11D)
        candidate = base / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / f"{tier}_longonly.parquet"
        if candidate.exists():
            return candidate
    return None


def choose_instrument(tier, nq_margin: float, mnq_margin: float):
    """Phase E rev: force NQ for ALL tiers regardless of margin.

    Backtest scale must match live PA L0 trader scale (1 NQ-equivalent =
    1 NQ for 25K, 2 NQ for 50K, etc — but backtest is per-1-contract).
    The legacy margin check (NQ_margin × 1.20 vs starting_balance) was
    overly conservative — actual Apex intraday margins are much lower
    than $23,282, and run_backtest does not enforce margin. Live trader
    at PA L0 25K trades 1 NQ on $25K capital (Apex permits).

    History:
      - Phase 0-13: 25K returned MNQ here (margin > capital). MC scaled
        ×10 via TIER_TO_PARQUET factor=10 to convert MNQ→NQ-eq.
      - Phase E rev: backtest at 1 NQ direct for ALL tiers. TIER_TO_PARQUET
        factor=1.0 uniform. DLL applied at backtest level as live-nominal $.
    """
    return "NQ", tier.max_contracts_nq, nq_margin


def daily_pnl_from_trades(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float, name="daily_pnl")
    s = trades.copy()
    s["exit_date"] = pd.to_datetime(s["exit_time"]).dt.normalize()
    return s.groupby("exit_date")["pnl"].sum().rename("daily_pnl")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--out-dir", default="reports/phase11e_sl_elbow")
    parser.add_argument("--mnq-margin", type=float, default=MNQ_MARGIN_ASSUMPTION)
    parser.add_argument("--nq-margin", type=float, default=NQ_MARGIN)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 11E.1] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts_by_name = {
        t.name: t for t in load_apex_accounts(args.accounts) if t.name in APEX_4_TIER_NAMES
    }

    # Build target combos and filter existing
    all_combos = [(tp, sl, tier) for tp in TPS for sl in SLS for tier in APEX_4_TIER_NAMES]
    new_combos = [
        (tp, sl, tier) for (tp, sl, tier) in all_combos
        if cell_exists_anywhere(tp, sl, tier) is None
    ]
    n_existing = len(all_combos) - len(new_combos)
    print(f"[Phase 11E.1] Total grid cells: {len(all_combos)}")
    print(f"[Phase 11E.1] Existing cells (11B + 11D): {n_existing}")
    print(f"[Phase 11E.1] NEW cells to compute: {len(new_combos)}")

    summary_rows = []
    for i, (tp, sl, tier_name) in enumerate(new_combos, 1):
        tier = accounts_by_name[tier_name]
        inst_name, max_contracts, margin = choose_instrument(
            tier, args.nq_margin, args.mnq_margin
        )
        instr = load_instrument_spec(inst_name)

        cell_dir = out / f"daily_pnl_tp{tp:03d}_sl{sl:05d}"
        cell_dir.mkdir(parents=True, exist_ok=True)

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
            daily_loss_limit=TIER_LIVE_DLL[tier_name],  # Phase E rev
        )
        res = run_backtest(df_5min, cfg, instr)
        pnl = daily_pnl_from_trades(res.trades)
        pnl_path = cell_dir / f"{tier_name}_longonly.parquet"
        pnl.to_frame().to_parquet(pnl_path)

        n_trades = len(res.trades)
        total_pnl = float(res.trades["pnl"].sum()) if not res.trades.empty else 0.0
        daily_std = float(pnl.std()) if len(pnl) > 1 else 0.0
        target_hits = int((res.trades["exit_reason"] == "target").sum()) if not res.trades.empty else 0
        stop_hits = int((res.trades["exit_reason"] == "stop").sum()) if not res.trades.empty else 0
        max_hold_hits = int((res.trades["exit_reason"] == "max_hold").sum()) if not res.trades.empty else 0

        summary_rows.append({
            "tier": tier_name,
            "tp_points": tp,
            "sl_points": sl,
            "instrument": inst_name,
            "max_contracts": max_contracts,
            "trades": n_trades,
            "target_hits": target_hits,
            "stop_hits": stop_hits,
            "max_hold_hits": max_hold_hits,
            "target_hit_rate": round(target_hits/n_trades, 3) if n_trades else 0.0,
            "stop_hit_rate": round(stop_hits/n_trades, 3) if n_trades else 0.0,
            "total_pnl": round(total_pnl, 0),
            "days_traded": len(pnl),
            "daily_std": round(daily_std, 0),
        })

        if i % 10 == 0 or i == len(new_combos):
            print(
                f"  [{i:3d}/{len(new_combos)}] {tier_name:10s} TP={tp:5d} SL={sl:5d} "
                f"{inst_name} | trades={n_trades:4d} tgt={target_hits/max(n_trades,1)*100:5.1f}% "
                f"stp={stop_hits/max(n_trades,1)*100:5.1f}% mh={max_hold_hits/max(n_trades,1)*100:5.1f}% "
                f"std=${daily_std:>6,.0f}"
            )

    summary = pd.DataFrame(summary_rows)
    summary_path = out / "full_grid_backtest_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\n[Phase 11E.1] Summary -> {summary_path} ({len(summary)} new rows)")


if __name__ == "__main__":
    main()
