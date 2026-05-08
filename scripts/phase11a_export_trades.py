"""Phase 11A.1: Export per-trade backtest with SL AND TP both disabled.

Goal: capture full max adverse excursion (MAE) of each trade BEFORE any SL
caps it. With SL=10000 and TP=10000 (effectively unreachable), trades exit
only via max_hold (5 days) or EOD force-flatten. Per-bar `mae_points` then
reflects the true peak adverse run during each trade's full life.

Output: `reports/phase11a_mae_sl/trades_baseline_no_sl_no_tp.parquet` with
full trade schema (mae_points, mfe_points, pnl, exit_reason, ...).

This is the input for `phase11a_mae_analysis.py` which computes MAE
percentiles to use as candidate SL values for the lifecycle MC sweep.
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
        description="Export per-trade backtest with SL+TP disabled to capture MAE."
    )
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--out-dir", default="reports/phase11a_mae_sl")
    parser.add_argument(
        "--sl-disabled",
        type=float,
        default=10000.0,
        help="Stop_points value to effectively disable SL.",
    )
    parser.add_argument(
        "--tp-disabled",
        type=float,
        default=10000.0,
        help="Target_points value to effectively disable TP.",
    )
    parser.add_argument("--instrument", default="NQ")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 11A.1] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    instr = load_instrument_spec(args.instrument)

    cfg = replace(
        base_config,
        stop_points=args.sl_disabled,
        target_points=args.tp_disabled,
        long_only=True,
        flatten_at_eod=True,
    )

    print(
        f"[Phase 11A.1] Backtest: SL={cfg.stop_points} (disabled), "
        f"TP={cfg.target_points} (disabled), instr={instr.symbol}"
    )
    res = run_backtest(df_5min, cfg, instr)
    trades = res.trades

    if trades.empty:
        raise RuntimeError("No trades produced; check data path and config.")

    out_path = out / "trades_baseline_no_sl_no_tp.parquet"
    trades.to_parquet(out_path)

    n = len(trades)
    total_pnl = float(trades["pnl"].sum())
    mae = trades["mae_points"]
    mfe = trades["mfe_points"]
    exit_counts = trades["exit_reason"].value_counts().to_dict()

    print(f"\n[Phase 11A.1] Trades: {n}")
    print(f"[Phase 11A.1] Total PnL (no SL no TP): ${total_pnl:,.0f}")
    print(f"[Phase 11A.1] Exit reasons: {exit_counts}")
    print(
        f"[Phase 11A.1] MAE points: min={mae.min():.1f} median={mae.median():.1f} "
        f"mean={mae.mean():.1f} q90={mae.quantile(0.9):.1f} max={mae.max():.1f}"
    )
    print(
        f"[Phase 11A.1] MFE points: min={mfe.min():.1f} median={mfe.median():.1f} "
        f"mean={mfe.mean():.1f} max={mfe.max():.1f}"
    )
    print(f"[Phase 11A.1] Saved -> {out_path}")


if __name__ == "__main__":
    main()
