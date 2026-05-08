"""Phase 7b: Per-tier backtests with correct position sizing.

Phase 7 bug: one backtest at $1M (NQ, ~5 contracts, daily_std ~$30K) was used
for ALL tiers. That is wrong -- a $1.5K trailing threshold cannot withstand
$30K daily vol. This script fixes it by running a separate backtest per tier,
with the tier's own starting capital, max_contracts cap, and correct instrument.

Instrument selection:
  - apex_25k: MNQ (NQ margin $26775 > $25K balance, NQ not viable)
  - apex_50k+: NQ (margin ~$26775, fits within balance)

MNQ margin assumption: $100/contract intraday (Apex evaluation account estimate).
THIS VALUE HAS NOT BEEN VERIFIED AGAINST LIVE APEX DOCUMENTATION.
Apex typically offers ~$50-200 intraday margin for MNQ eval accounts.
If the real figure differs, re-run with the correct --mnq-margin value.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import StrategyConfig, load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest

NQ_MARGIN = 23282.57
# Apex intraday margin estimate for MNQ (NOT verified from live Apex docs).
# Typical range: $50-200. Using $100 as a conservative midpoint.
MNQ_MARGIN_ASSUMPTION = 100.0


def choose_instrument(tier, nq_margin: float, mnq_margin: float):
    """Return (instrument_name, max_contracts, margin) for a tier.

    Rule: if starting_balance < NQ_MARGIN * 1.20, NQ is not safely affordable
    and we fall back to MNQ. 1.20 = 1 + margin_buffer (0.15) + safety pad (0.05).
    """
    required_nq = nq_margin * 1.20  # = ~27930
    if tier.starting_balance >= required_nq:
        return "NQ", tier.max_contracts_nq, nq_margin
    return "MNQ", tier.max_contracts_mnq, mnq_margin


def daily_pnl_from_trades(trades: pd.DataFrame) -> pd.Series:
    """Sum trade pnl by exit date."""
    if trades.empty:
        return pd.Series(dtype=float, name="daily_pnl")
    s = trades.copy()
    s["exit_date"] = pd.to_datetime(s["exit_time"]).dt.normalize()
    return s.groupby("exit_date")["pnl"].sum().rename("daily_pnl")


def main():
    parser = argparse.ArgumentParser(
        description="Run per-tier backtests for Apex Phase 7b."
    )
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--out-dir", default="data/daily_pnl_per_tier")
    parser.add_argument(
        "--mnq-margin",
        type=float,
        default=MNQ_MARGIN_ASSUMPTION,
        help=(
            "Intraday margin per MNQ contract in USD. "
            "Default 100.0 is an UNVERIFIED estimate for Apex eval accounts. "
            "Confirm against live Apex documentation before drawing conclusions."
        ),
    )
    parser.add_argument(
        "--nq-margin",
        type=float,
        default=NQ_MARGIN,
        help="Intraday margin per NQ contract in USD.",
    )
    args = parser.parse_args()

    print(
        f"[ASSUMPTION] MNQ intraday margin = ${args.mnq_margin:.0f}/contract "
        "(NOT verified from Apex live documentation -- confirm before live use)."
    )

    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts = load_apex_accounts(args.accounts)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for tier in accounts:
        inst_name, max_contracts, margin = choose_instrument(
            tier, args.nq_margin, args.mnq_margin
        )
        instr = load_instrument_spec(inst_name)

        config: StrategyConfig = replace(
            base_config,
            initial_capital=tier.starting_balance,
            max_position=max_contracts,
            margin_per_contract=margin,
            flatten_at_eod=True,
            eod_cutoff_time=tier.flatten_cutoff,
            long_only=True,
        )

        result = run_backtest(df_5min, config, instr)
        pnl = daily_pnl_from_trades(result.trades)

        pnl_path = out / f"{tier.name}_longonly.parquet"
        pnl.to_frame().to_parquet(pnl_path)

        daily_std = float(pnl.std()) if len(pnl) > 1 else 0.0
        daily_mean = float(pnl.mean()) if len(pnl) else 0.0
        total_pnl = float(result.trades["pnl"].sum()) if not result.trades.empty else 0.0

        rows.append(
            {
                "tier": tier.name,
                "instrument": inst_name,
                "start_capital": tier.starting_balance,
                "max_contracts": max_contracts,
                "margin_per_contract": margin,
                "trades": len(result.trades),
                "total_pnl": total_pnl,
                "days_traded": len(pnl),
                "daily_mean": daily_mean,
                "daily_std": daily_std,
                "daily_min": float(pnl.min()) if len(pnl) else 0.0,
                "daily_max": float(pnl.max()) if len(pnl) else 0.0,
            }
        )
        print(
            f"{tier.name}: {inst_name} {max_contracts}c | "
            f"{len(result.trades)} trades | "
            f"total ${total_pnl:,.0f} | "
            f"daily_std ${daily_std:,.0f} | "
            f"trailing_threshold ${tier.trailing_threshold:,.0f}"
        )

    summary = pd.DataFrame(rows)
    summary_path = out / "per_tier_backtest_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
