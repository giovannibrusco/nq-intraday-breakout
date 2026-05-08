"""Phase 10A.1: Export per-trade backtest with TP effectively disabled.

Goal: capture full max favorable excursion (MFE) of each trade BEFORE any TP
caps it. With TP=10000 points (effectively unreachable), trades exit only via
SL, EOD force-flatten, or max_hold. Per-bar `mfe_points` then reflects the true
peak favorable run during each trade.

Output: `reports/phase10a_mfe_tp/trades_baseline_no_tp.parquet` with full trade
schema (direction, entry/exit, mfe_points, mae_points, pnl, exit_reason, ...).

This is the input for `phase10a_mfe_analysis.py` which computes MFE percentiles
to use as candidate TP values for the lifecycle MC sweep.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest


def main():
    parser = argparse.ArgumentParser(
        description="Export per-trade backtest with TP disabled to capture MFE."
    )
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--out-dir", default="reports/phase10a_mfe_tp")
    parser.add_argument(
        "--tp-disabled",
        type=float,
        default=10000.0,
        help="Target_points value used to effectively disable TP (let trades run to SL/EOD).",
    )
    parser.add_argument(
        "--instrument",
        default="NQ",
        help="Instrument symbol (NQ or MNQ). Strategy is per-tick, so MFE in points is identical.",
    )
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 10A.1] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    instr = load_instrument_spec(args.instrument)

    cfg = replace(
        base_config,
        target_points=args.tp_disabled,
        long_only=True,
        flatten_at_eod=True,  # Apex EOD cutoff (keeps trades intraday)
    )

    print(
        f"[Phase 10A.1] Running backtest: TP={cfg.target_points} (disabled), "
        f"SL={cfg.stop_points}, instr={instr.symbol}"
    )
    res = run_backtest(df_5min, cfg, instr)
    trades = res.trades

    if trades.empty:
        raise RuntimeError("No trades produced; check data path and config.")

    out_path = out / "trades_baseline_no_tp.parquet"
    trades.to_parquet(out_path)

    # Summary diagnostics
    n = len(trades)
    total_pnl = float(trades["pnl"].sum())
    mfe = trades["mfe_points"]
    mae = trades["mae_points"]
    exit_counts = trades["exit_reason"].value_counts().to_dict()

    print(f"\n[Phase 10A.1] Trades: {n}")
    print(f"[Phase 10A.1] Total PnL (no TP): ${total_pnl:,.0f}")
    print(f"[Phase 10A.1] Exit reasons: {exit_counts}")
    print(
        f"[Phase 10A.1] MFE points: min={mfe.min():.1f} median={mfe.median():.1f} "
        f"mean={mfe.mean():.1f} max={mfe.max():.1f}"
    )
    print(
        f"[Phase 10A.1] MAE points: min={mae.min():.1f} median={mae.median():.1f} "
        f"mean={mae.mean():.1f} max={mae.max():.1f}"
    )
    print(f"[Phase 10A.1] Saved -> {out_path}")


if __name__ == "__main__":
    main()
