"""Regenerate the Phase E rev fixture as a TRADE-LEVEL parquet enriched
with a lookahead-free ATR(14) volatility index per trade.

Why this script exists
======================

The standard Phase 11E grid script (``scripts/phase11e_full_grid_backtest.py``)
saves the daily-aggregated PnL series for each (TP, SL, tier) cell. That
representation is sufficient for daily block-bootstrap Monte Carlo, but is
too coarse for the prop-evaluator v0.2 simulator that operates on a
per-trade Markov chain conditioned on a volatility regime. We therefore
re-run a single cell — ``TP=82, SL=88, apex_25k_longonly`` — and emit the
unaggregated trades DataFrame plus a daily ATR(14) series so downstream
tooling can attach a regime label to every trade.

What is computed
================

1. Daily OHLC of NQ from the 1-minute parquet, restricted to the regular
   trading session 09:30 .. 16:00 ET.

2. ATR(14) computed via simple moving average of True Range, then shifted
   by exactly one trading day so the value at row ``d`` is the ATR
   AS-OF END-OF-DAY ``d - 1``. This guarantees no look-ahead bias when
   joined onto the trade log.

3. Backtest at TP=82, SL=88, ``apex_25k`` tier, long-only, ``daily_loss_limit
   = $500``, EOD flatten at the tier's ``flatten_cutoff``. Mirrors the
   exact configuration of the corresponding Phase 11E cell.

4. Each trade row is enriched with:
      - ``side``                  long/short
      - ``symbol``, ``point_value`` constants for NQ
      - ``mae``                   signed-negative dollar MAE
      - ``mfe``                   signed-positive dollar MFE
      - ``atr14_pts``             ATR(14) in points, lookahead-free
      - ``atr14_dollars``         atr14_pts * point_value * contracts
      - ``entry_date``            convenience for joining

Trades whose ``entry_date`` falls inside the 14-day ATR warm-up are dropped
(the count is printed for transparency; expected to be a tiny fraction).

Output paths
============

- ``reports/phase_e_volatility/trades_phase_e_tp082_sl00088.parquet``
- ``reports/phase_e_volatility/atr14_nq_daily.parquet``

Both files can then be symlinked into the prop-evaluator test fixture
directory.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.data.volatility import (
    aggregate_to_daily_ohlc,
    compute_atr14_lookahead_free,
)
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import (
    load_instrument_spec,
    load_strategy_config,
)
from nqbreakout.strategy.core import run_backtest


NQ_MARGIN = 23282.57
TIER_NAME = "apex_25k"
TIER_LIVE_DLL = 500.0  # mirrors phase11e_full_grid_backtest.py
TARGET_POINTS = 82
STOP_POINTS = 88


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--data",
        default="data/nq_1min.parquet",
        help="Path to the NQ 1-minute parquet.",
    )
    p.add_argument(
        "--config",
        default="configs/strategy_default.yaml",
        help="Base StrategyConfig YAML (overridden by TP/SL/tier values below).",
    )
    p.add_argument(
        "--accounts",
        default="configs/apex_accounts.yaml",
        help="Apex accounts YAML.",
    )
    p.add_argument(
        "--out-dir",
        default="reports/phase_e_volatility",
        help="Directory for the output parquet files.",
    )
    p.add_argument(
        "--atr-window",
        type=int,
        default=14,
        help="ATR window length in days (default 14).",
    )
    p.add_argument(
        "--atr-method",
        choices=("sma", "wilder"),
        default="sma",
        help="ATR smoothing: simple moving average (default) or Wilder.",
    )
    return p.parse_args()


def _build_strategy_config(base_path: str, tier_obj, *, tp: int, sl: int):
    """Return a StrategyConfig matching the Phase 11E cell for this tier."""
    base_config = load_strategy_config(base_path)
    return replace(
        base_config,
        target_points=float(tp),
        stop_points=float(sl),
        initial_capital=tier_obj.starting_balance,
        max_position=tier_obj.max_contracts_nq,
        margin_per_contract=NQ_MARGIN,
        flatten_at_eod=True,
        eod_cutoff_time=tier_obj.flatten_cutoff,
        long_only=True,
        daily_loss_limit=TIER_LIVE_DLL,
    )


def _enrich_trades_with_volatility(
    trades: pd.DataFrame,
    atr_lookahead_free: pd.Series,
    *,
    point_value: float,
    symbol: str,
) -> pd.DataFrame:
    """Add prop-evaluator-compatible columns + lookahead-free ATR per trade."""
    out = trades.copy()
    out["side"] = out["direction"]
    out["symbol"] = symbol
    out["point_value"] = float(point_value)

    # Signed MAE/MFE in dollars: schema convention is MAE <= 0, MFE >= 0.
    contracts = out["contracts"].astype(float)
    out["mae"] = -out["mae_points"].astype(float) * point_value * contracts
    out["mfe"] = out["mfe_points"].astype(float) * point_value * contracts

    # Trade-level ATR via the entry date.
    out["entry_date"] = pd.to_datetime(out["entry_time"]).dt.date
    out["atr14_pts"] = out["entry_date"].map(atr_lookahead_free)
    out["atr14_dollars"] = out["atr14_pts"] * point_value * contracts

    return out


def _print_summary(
    *,
    n_total: int,
    n_dropped: int,
    out: pd.DataFrame,
    atr: pd.Series,
) -> None:
    print()
    print("=" * 72)
    print("Phase E rev volatility-enriched trade log")
    print("=" * 72)
    print(f"Trades total      : {n_total}")
    print(f"Trades dropped    : {n_dropped} (entry_date inside ATR warm-up)")
    print(f"Trades kept       : {len(out)}")
    print()
    print("ATR(14) percentiles (points, lookahead-free):")
    pct = atr.dropna().quantile([0.05, 0.25, 0.50, 0.75, 0.95])
    for q, v in pct.items():
        print(f"  p{int(q*100):02d} : {v:>8.2f}")
    print()

    if out.empty:
        return

    # Win rate by ATR tertile — sanity preview of the regime structure that
    # the prop-evaluator's regime detector will pick up.
    out_with_regime = out.copy()
    out_with_regime["regime"] = pd.qcut(
        out_with_regime["atr14_pts"], q=3, labels=["low", "mid", "high"]
    )
    out_with_regime["winner"] = out_with_regime["pnl"] > 0
    win_by_regime = out_with_regime.groupby("regime", observed=True)["winner"].agg(
        ["mean", "count"]
    )
    print("Win rate by ATR tertile (regime preview):")
    for regime_label, row in win_by_regime.iterrows():
        print(f"  {regime_label!s:<5} : {row['mean']:>5.2%}  (n={int(row['count']):>5d})")
    print()


def main() -> int:
    args = _parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[volatility] Loading 1-min data: {args.data}")
    df_1min = load_nq_1min(args.data)
    df_5min = resample_to_5min(df_1min)

    print("[volatility] Aggregating to daily OHLC (RTH 09:30 .. 16:00) ...")
    daily_ohlc = aggregate_to_daily_ohlc(df_5min)

    print(
        f"[volatility] Computing ATR({args.atr_window}) lookahead-free "
        f"(method={args.atr_method}) ..."
    )
    atr = compute_atr14_lookahead_free(
        daily_ohlc, window=args.atr_window, method=args.atr_method
    )

    # Persist the daily ATR series for external consumers.
    atr_path = out_dir / "atr14_nq_daily.parquet"
    atr.dropna().to_frame().to_parquet(atr_path)
    print(f"[volatility] Saved daily ATR -> {atr_path}")

    accounts = {t.name: t for t in load_apex_accounts(args.accounts)}
    if TIER_NAME not in accounts:
        print(
            f"ERROR: tier {TIER_NAME!r} not found in {args.accounts}",
            file=sys.stderr,
        )
        return 2
    tier = accounts[TIER_NAME]

    cfg = _build_strategy_config(args.config, tier, tp=TARGET_POINTS, sl=STOP_POINTS)
    instr = load_instrument_spec("NQ")
    print(
        f"[volatility] Backtest TP={TARGET_POINTS} SL={STOP_POINTS} "
        f"tier={TIER_NAME} long_only=True dll=${TIER_LIVE_DLL:.0f} ..."
    )
    res = run_backtest(df_5min, cfg, instr)

    if res.trades.empty:
        print("WARN: backtest produced 0 trades. Aborting.", file=sys.stderr)
        return 3

    enriched = _enrich_trades_with_volatility(
        res.trades,
        atr,
        point_value=instr.point_value,
        symbol=instr.symbol,
    )

    n_total = len(enriched)
    mask_no_atr = enriched["atr14_pts"].isna()
    n_dropped = int(mask_no_atr.sum())
    out = enriched.loc[~mask_no_atr].reset_index(drop=True)

    # Sanity: required columns for prop-evaluator's TradeRecord schema must
    # all be non-null in the kept slice.
    for col in ("entry_time", "exit_time", "side", "pnl"):
        assert out[col].notna().all(), f"unexpected NaN in required column {col!r}"
    assert (out["atr14_pts"] > 0).all(), "ATR must be strictly positive"
    assert (out["mae"] <= 0).all(), "MAE must be signed-negative"
    assert (out["mfe"] >= 0).all(), "MFE must be signed-positive"

    trades_path = out_dir / "trades_phase_e_tp082_sl00088.parquet"
    out.to_parquet(trades_path)
    print(f"[volatility] Saved enriched trades -> {trades_path}")

    _print_summary(n_total=n_total, n_dropped=n_dropped, out=out, atr=atr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
