"""Phase 13.2: Re-run backtest + lifecycle MC for 8 aggressive setups
with Apex Rithmic instrument-aware cost model (post-Phase 13.1 fix).

For each Phase 11E aggressive setup, re-runs:
  - backtest with NQ_RITHMIC ($1.99/leg) or MNQ_RITHMIC ($0.51/leg)
  - lifecycle MC 5000 sims
  - compares E[net]/cycle vs original (pre-cost-fix) numbers

Output:
  reports/phase13_cost_fix/cost_corrected_summary.csv  (8 rows)
  reports/phase13_cost_fix/daily_pnl_{product}.parquet  x 8
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts, load_apex_products
from nqbreakout.prop.montecarlo import run_lifecycle_sweep
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, summarize  # noqa: E402

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
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--elbow-summary",
                        default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase13_cost_fix")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 13.2] Loading 5min data...")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts_by_name = {
        t.name: t for t in load_apex_accounts(args.accounts)
        if t.name in APEX_4_TIER_NAMES
    }
    products_by_name = {p.name: p for p in load_apex_products(args.products)}
    elbow = pd.read_csv(args.elbow_summary)

    rows = []
    for _, e in elbow.iterrows():
        product_name = e["product"]
        tp = int(e["best_tp_global"])
        sl = int(e["best_sl_global"])
        original_e_net = float(e["best_E_net_global"])

        if product_name not in TIER_MAP:
            continue
        tier_short, tier_acc_name, variant = TIER_MAP[product_name]
        tier = accounts_by_name[tier_acc_name]
        product = products_by_name[product_name]

        inst_name, max_contracts, margin = choose_instrument(
            tier, NQ_MARGIN, MNQ_MARGIN
        )
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
        # cost_model defaults to instrument-aware (NQ_RITHMIC or MNQ_RITHMIC) via run_backtest
        print(f"\n[Phase 13.2] {product_name}: TP={tp} SL={sl} {inst_name}")
        res = run_backtest(df_5min, cfg, instr)
        n_trades = len(res.trades)
        gross_pnl = float(res.trades["pnl"].sum()) if n_trades else 0.0

        pnl = daily_pnl_from_trades(res.trades)
        # Apply NQ-equivalent factor for MC
        factor = TIER_TO_PARQUET[tier_short][1]
        pnl_mc = pnl * factor

        # Save cost-corrected daily PnL
        pnl_path = out / f"daily_pnl_{product_name}.parquet"
        pnl.to_frame().to_parquet(pnl_path)

        # Run lifecycle MC
        df_mc = run_lifecycle_sweep(
            pnl_per_mini=pnl_mc,
            product=product,
            eval_contracts_mini=1.0,
            n_sims=args.sims,
            pa_max_days=504,
            block_size=args.block_size,
            seed=args.seed,
        )
        s = summarize(df_mc, product)

        new_e_net = float(s.get("expected_net_per_cycle_promo", 0))
        diff = new_e_net - original_e_net
        diff_pct = 100.0 * diff / original_e_net if original_e_net else 0.0

        rows.append({
            "product": product_name,
            "tier": tier_short,
            "variant": variant,
            "tp": tp, "sl": sl,
            "instrument": inst_name,
            "n_trades": n_trades,
            "gross_pnl_total": round(gross_pnl, 0),
            "pass_rate": round(s["pass_rate"], 4),
            "E_net_per_cycle_promo_NEW": round(new_e_net, 0),
            "E_net_per_cycle_promo_OLD": round(original_e_net, 0),
            "delta_E_net_$": round(diff, 0),
            "delta_E_net_pct": round(diff_pct, 2),
            "E_cycle_days": round(float(s["expected_cycle_days"]), 1),
            "annualized_promo_NEW": round(float(s["annualized_net_promo"]), 0),
        })
        print(
            f"  trades={n_trades}  gross=${gross_pnl:,.0f}  pass={s['pass_rate']:.3f}  "
            f"E[net]=${new_e_net:,.0f} (was ${original_e_net:,.0f}, "
            f"Δ={diff:+,.0f} = {diff_pct:+.1f}%)"
        )

    summary = pd.DataFrame(rows).sort_values("E_net_per_cycle_promo_NEW", ascending=False)
    summary_path = out / "cost_corrected_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\n[Phase 13.2] Summary -> {summary_path}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
