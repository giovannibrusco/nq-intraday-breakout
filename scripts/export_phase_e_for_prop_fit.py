"""Export Phase E backtest results in prop-fit optimizer contract v1 format.

Produces two parquet files conforming to the contract documented in
``~/Desktop/prop-evaluator/docs/backtest-output-contract.md``:

- ``trades.parquet``      One row per closed baseline trade
                          (schema: ``PropFitTradeRecord``).
- ``trade_paths.parquet`` One row per 1-min OHLC bar between ``entry_time``
                          and ``path_end_time`` per trade
                          (schema: ``TradePathRecord``).

The optimizer uses ``trade_paths`` to replay alternative TP/SL combinations
on the same baseline trades — hence the path must extend BEYOND the baseline
``exit_time`` up to ``path_end_time`` (here: the RTH session close at 16:00
ET on the exit day).

Conventions
===========

- ``timezone = "America/New_York"`` — the underlying NQ 1-min parquet stores
  timestamps tz-naive but represents US/Eastern (see ``loader.py`` docstring).
- ``session_date`` = ``entry_time.date()`` in US/Eastern.
- ``entry_time`` is the **post-fill** timestamp, NOT the strategy's signal-bar
  timestamp. The Phase E strategy emits signals at the close of each 5-min
  bar (which is timestamped at bar start with ``label="left", closed="left"``
  in the resampler). The actual fill conceptually happens at the start of
  the next 1-min bar. This exporter shifts ``entry_time`` by
  ``STRATEGY_BAR_MINUTES`` (=5 min) compared to the raw backtest output so
  that ``entry_time`` represents the first tradable moment after the signal
  and the path invariant ``min(trade_paths.bar_time) >= entry_time`` holds.
  Note: ``entry_price`` is unchanged — it remains the 5-min signal bar's
  close, which is the strategy's assumed fill price at that moment. There is
  a small theoretical mismatch (entry_price is the price at signal-bar close
  ~ 10:14:59 for a 10:10 signal, while entry_time is the start of the next
  1-min bar = 10:15:00); for replay purposes this is harmless because the
  optimizer uses entry_price only as the TP/SL reference.
- ``path_end_time`` = the last 1-min bar at or before 16:00 ET on
  ``exit_time``'s date, restricted to bars in [entry_time, RTH_close] and
  inside the RTH session (09:30..16:00 ET). The validator requires
  ``path_end_time`` to equal the last ``bar_time`` of the path bars exactly.
- ``exit_reason`` mapping (legacy → contract):
    stop        → sl
    target      → tp
    force_close → session_close
    eod_short   → session_close
    max_hold    → timeout
    dll_flatten → dll
- Sign conventions (enforced by validator):
    ``mae_points`` ≤ 0     ``mfe_points`` ≥ 0
    ``mae``        ≤ 0     ``mfe``        ≥ 0
    ``commission`` ≥ 0     ``slippage``   ≥ 0
- ``pnl_points`` = ``pnl_per_contract_net / point_value`` (signed). Note: this
  is per-contract semantics consistent with the legacy backtest output where
  ``pnl_per_contract_net`` is the net per-contract dollar PnL.
- SL-first convention for same-bar TP/SL: this script does NOT need to apply
  SL-first explicitly because the strategy logic in ``core.py`` checks stops
  before targets (lines 467-486). The 1-min path bars provided to the
  optimizer are raw OHLC — the optimizer applies SL-first when replaying.

Strategy config
===============

Mirrors ``scripts/regenerate_phase_e_trades_with_volatility.py``:
- TP=82 points, SL=88 points
- ``apex_25k`` tier, long-only
- ``daily_loss_limit = $500`` (Apex live DLL semantics)
- EOD flatten at tier ``flatten_cutoff``
- 5-min bars for backtest entries/exits
- 1-min bars for trade_paths output

This script does NOT modify Phase E strategy logic; it only changes the
exporter output format to match the prop-fit contract.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest


# --- Constants (match Phase E config) ---------------------------------------

NQ_MARGIN = 23282.57
TIER_NAME = "apex_25k"
TIER_LIVE_DLL = 500.0  # mirrors scripts/regenerate_phase_e_trades_with_volatility.py
TARGET_POINTS = 82
STOP_POINTS = 88

# --- Contract conventions ---------------------------------------------------

TIMEZONE = "America/New_York"
RTH_OPEN = dt.time(9, 30)
RTH_CLOSE = dt.time(16, 0)
BAR_INTERVAL_SECONDS = 60  # 1-min bars for path
STRATEGY_BAR_MINUTES = 5  # the Phase E backtest operates on 5-min bars; raw
# trades' entry_time is the bar-start timestamp of the signal bar, so we
# shift entry_time by +5min in the export to represent the post-fill moment
# (first 1-min bar after the signal bar's close).

EXIT_REASON_MAP: dict[str, str] = {
    "stop": "sl",
    "target": "tp",
    "force_close": "session_close",
    "eod_short": "session_close",
    "max_hold": "timeout",
    "dll_flatten": "dll",
}


# --- CLI --------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--data",
        default="data/nq_1min.parquet",
        help="NQ 1-minute parquet path.",
    )
    p.add_argument(
        "--config",
        default="configs/strategy_default.yaml",
        help="Base StrategyConfig YAML (TP/SL/tier values overridden below).",
    )
    p.add_argument(
        "--accounts",
        default="configs/apex_accounts.yaml",
        help="Apex accounts YAML.",
    )
    p.add_argument(
        "--out-dir",
        default="reports/phase_e_prop_fit",
        help="Output directory for trades.parquet and trade_paths.parquet.",
    )
    p.add_argument(
        "--skip-validator",
        action="store_true",
        help="Skip running the prop-evaluator validator at the end "
        "(useful in CI or when prop-evaluator's venv is unavailable).",
    )
    return p.parse_args()


# --- Strategy config builder ------------------------------------------------


def _build_strategy_config(base_path: str, tier_obj, *, tp: int, sl: int):
    """Phase E config: same as regenerate_phase_e_trades_with_volatility.py."""
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


# --- Trade id generation ----------------------------------------------------


def _generate_trade_id(entry_time: pd.Timestamp, side: str) -> str:
    """Stable deterministic id: T-YYYYMMDD-HHMMSS-[LS].

    Uses entry timestamp + first letter of side. Phase E allows at most one
    open trade at a time and bounded ``max_trades_per_day``, so collisions on
    (entry_time, side) are impossible by construction.
    """
    return f"T-{entry_time.strftime('%Y%m%d-%H%M%S')}-{side[0].upper()}"


# --- trades.parquet builder -------------------------------------------------


def _build_trades_df(
    raw_trades: pd.DataFrame, *, point_value: float, symbol: str
) -> pd.DataFrame:
    """Convert raw backtest trades to contract-compliant trades schema.

    Note: ``path_end_time`` is NOT set here — it's filled in by
    ``_build_paths_df`` after the path bars are computed, because the validator
    requires ``path_end_time`` to equal the bar_time of the LAST path bar.
    """
    out = pd.DataFrame()
    # entry_time is shifted to the post-fill convention: raw entry_time is
    # the START of the 5-min signal bar, but the actual fill is at the
    # close of that bar (= start of the next 1-min bar). Shift by
    # STRATEGY_BAR_MINUTES so that min(path bar_time) >= entry_time holds.
    out["entry_time"] = pd.to_datetime(
        raw_trades["entry_time"]
    ) + pd.Timedelta(minutes=STRATEGY_BAR_MINUTES)
    out["exit_time"] = pd.to_datetime(raw_trades["exit_time"])
    out["side"] = raw_trades["direction"].astype(str)

    contracts = raw_trades["contracts"].astype(float)
    out["contracts"] = contracts
    out["entry_price"] = raw_trades["entry_price"].astype(float)
    out["exit_price"] = raw_trades["exit_price"].astype(float)

    out["pnl"] = raw_trades["pnl"].astype(float)
    out["point_value"] = float(point_value)
    out["symbol"] = str(symbol)
    out["timezone"] = TIMEZONE
    out["session_date"] = out["entry_time"].dt.date

    # pnl_points: signed PnL in points (per contract).
    out["pnl_points"] = raw_trades["pnl_per_contract_net"].astype(float) / float(
        point_value
    )

    # MAE/MFE sign conventions:
    # - raw_trades stores mae_points/mfe_points as POSITIVE magnitudes
    #   (max excursions tracked in core.py:386-397).
    # - contract requires mae_points ≤ 0 and mfe_points ≥ 0 (signed).
    raw_mae_pts = raw_trades["mae_points"].astype(float)
    raw_mfe_pts = raw_trades["mfe_points"].astype(float)
    out["mae_points"] = -raw_mae_pts.abs()  # signed negative
    out["mfe_points"] = raw_mfe_pts.abs()  # signed positive
    out["mae"] = out["mae_points"] * float(point_value) * contracts  # dollars
    out["mfe"] = out["mfe_points"] * float(point_value) * contracts  # dollars

    # Costs (positive). fees_per_contract and slippage_per_contract come from
    # core.py:185-187 split of cost_model.total_round_trip_cost(reason).
    out["commission"] = (
        raw_trades["fees_per_contract"].astype(float).abs() * contracts
    )
    out["slippage"] = (
        raw_trades["slippage_per_contract"].astype(float).abs() * contracts
    )

    # Exit reason mapping to contract vocabulary.
    out["exit_reason"] = (
        raw_trades["exit_reason"]
        .astype(str)
        .map(EXIT_REASON_MAP)
        .fillna(raw_trades["exit_reason"].astype(str))
    )

    # Stable deterministic trade_id.
    out["trade_id"] = [
        _generate_trade_id(pd.Timestamp(et), side)
        for et, side in zip(out["entry_time"], out["side"])
    ]

    return out


# --- trade_paths.parquet builder --------------------------------------------


def _build_paths_df(
    trades_df: pd.DataFrame, df_1min: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build trade_paths DataFrame and finalize trades.path_end_time.

    For each trade, includes all 1-min OHLC bars present in the data between
    ``entry_time`` and the last RTH bar of ``exit_time.date()`` (typically
    around 15:59 ET). The validator requires the last bar_time per trade to
    equal ``path_end_time``, so this function sets ``path_end_time`` to the
    actual last included bar_time, not a synthetic "16:00 ET" timestamp.
    """
    # Use only RTH bars (09:30..16:00 inclusive) to avoid extended-hours
    # contamination of the path. Phase E entries and exits all live inside
    # RTH, so this filter is a no-op for entries; it only affects path
    # extension after baseline exit.
    bars = df_1min.set_index("date").sort_index()
    bars_time = bars.index.time
    rth_mask = (bars_time >= RTH_OPEN) & (bars_time <= RTH_CLOSE)
    rth_bars = bars.loc[rth_mask]

    path_rows: list[dict] = []
    path_end_times: dict[str, pd.Timestamp] = {}

    for _, trade in trades_df.iterrows():
        entry_t = pd.Timestamp(trade["entry_time"])
        exit_t = pd.Timestamp(trade["exit_time"])
        # Path extends from entry to RTH close of EXIT day. For an intraday
        # trade (entry day == exit day), this gives ~2-6h of post-exit
        # replay data inside the same session.
        rth_close_exit_day = pd.Timestamp(
            dt.datetime.combine(exit_t.date(), RTH_CLOSE)
        )
        # Slice RTH bars in [entry_time, rth_close_exit_day].
        slice_df = rth_bars.loc[entry_t:rth_close_exit_day]
        if slice_df.empty:
            raise ValueError(
                f"No RTH 1-min bars found for trade_id={trade['trade_id']!r} "
                f"between {entry_t} and {rth_close_exit_day}. "
                f"Check that the underlying 1-min parquet covers this range."
            )

        last_bar_time = pd.Timestamp(slice_df.index[-1])
        path_end_times[trade["trade_id"]] = last_bar_time

        # Build path rows. bar_index is monotonic from 0.
        for bar_index, (bar_time, bar_row) in enumerate(slice_df.iterrows()):
            path_rows.append(
                {
                    "trade_id": trade["trade_id"],
                    "bar_time": pd.Timestamp(bar_time),
                    "bar_index": int(bar_index),
                    "bar_interval_seconds": int(BAR_INTERVAL_SECONDS),
                    "open": float(bar_row["open"]),
                    "high": float(bar_row["high"]),
                    "low": float(bar_row["low"]),
                    "close": float(bar_row["close"]),
                }
            )

    paths_df = pd.DataFrame(path_rows)

    # Inject path_end_time into trades_df.
    trades_df = trades_df.copy()
    trades_df["path_end_time"] = trades_df["trade_id"].map(path_end_times)
    trades_df["path_end_time"] = pd.to_datetime(trades_df["path_end_time"])

    return trades_df, paths_df


# --- Reordering to contract column order ------------------------------------

_TRADE_COLS_ORDER = [
    "trade_id",
    "symbol",
    "side",
    "entry_time",
    "exit_time",
    "path_end_time",
    "entry_price",
    "exit_price",
    "contracts",
    "point_value",
    "pnl",
    "pnl_points",
    "mae",
    "mfe",
    "mae_points",
    "mfe_points",
    "commission",
    "slippage",
    "exit_reason",
    "session_date",
    "timezone",
]

_PATH_COLS_ORDER = [
    "trade_id",
    "bar_time",
    "bar_index",
    "bar_interval_seconds",
    "open",
    "high",
    "low",
    "close",
]


# --- Path/entry invariant check ---------------------------------------------


def _assert_no_pre_fill_path_bars(
    trades_df: pd.DataFrame, paths_df: pd.DataFrame
) -> None:
    """Assert that no path bar precedes its trade's (shifted) entry_time.

    The contract requires ``min(trade_paths.bar_time) >= entry_time`` per
    trade after the post-fill shift. Violations would mean a path bar
    represents a moment when the trade did not yet exist, which would lead
    to spurious TP/SL replays in the optimizer.

    Raises:
        AssertionError: with a per-trade breakdown of the worst violations.
    """
    entry_by_id = trades_df.set_index("trade_id")["entry_time"]
    min_bar_by_id = paths_df.groupby("trade_id", sort=False)["bar_time"].min()
    joined = pd.DataFrame(
        {"entry_time": entry_by_id, "min_bar_time": min_bar_by_id}
    ).dropna()
    violations = joined[joined["min_bar_time"] < joined["entry_time"]]
    if len(violations) > 0:
        worst = violations.head(5).to_string()
        raise AssertionError(
            f"Pre-fill path bars detected for {len(violations)} trade(s). "
            f"min(bar_time) must be >= entry_time per trade. First 5:\n{worst}"
        )


# --- Validator (best-effort import; falls back to subprocess) ---------------


def _run_validator(trades_path: Path, paths_path: Path) -> tuple[int, int] | None:
    """Run prop_evaluator.loaders.csv_loader.load_prop_fit_dataset.

    Returns (n_trades, n_paths) on success, raises on failure.
    Tries direct import first (if prop_evaluator is installed in this venv);
    falls back to subprocess against prop-evaluator's own venv if available.
    Returns None and prints a skip message if neither path works.
    """
    # Path A: direct import (if prop_evaluator available in this venv).
    try:
        from prop_evaluator.loaders.csv_loader import (  # type: ignore[import]
            load_prop_fit_dataset,
        )
    except ImportError:
        pass
    else:
        t, p = load_prop_fit_dataset(str(trades_path), str(paths_path))
        return len(t), len(p)

    # Path B: subprocess into prop-evaluator's venv.
    import subprocess

    prop_eval_root = Path.home() / "Desktop" / "prop-evaluator"
    candidate_python = prop_eval_root / ".venv" / "bin" / "python"
    if not candidate_python.exists():
        print(
            "[prop-fit] WARN: prop-evaluator venv not found at "
            f"{candidate_python}; skipping validator. Run the validator "
            "manually from prop-evaluator with: "
            f"python -c 'from prop_evaluator.loaders.csv_loader import "
            f"load_prop_fit_dataset; t, p = load_prop_fit_dataset("
            f'"{trades_path}", "{paths_path}"); '
            f"print(len(t), len(p))'",
            file=sys.stderr,
        )
        return None

    code = (
        "from prop_evaluator.loaders.csv_loader import load_prop_fit_dataset; "
        f"t, p = load_prop_fit_dataset({str(trades_path)!r}, {str(paths_path)!r}); "
        "print(f'{len(t)} {len(p)}')"
    )
    proc = subprocess.run(
        [str(candidate_python), "-c", code],
        capture_output=True,
        text=True,
        cwd=str(prop_eval_root),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Validator failed (rc={proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    n_t, n_p = proc.stdout.strip().split()
    return int(n_t), int(n_p)


# --- Main -------------------------------------------------------------------


def main() -> int:
    args = _parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[prop-fit] Loading 1-min data: {args.data}")
    df_1min = load_nq_1min(args.data)
    df_5min = resample_to_5min(df_1min)
    print(f"[prop-fit] 1-min bars: {len(df_1min):,} | 5-min bars: {len(df_5min):,}")

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
        f"[prop-fit] Backtest TP={TARGET_POINTS} SL={STOP_POINTS} "
        f"tier={TIER_NAME} long_only=True dll=${TIER_LIVE_DLL:.0f}"
    )
    res = run_backtest(df_5min, cfg, instr)

    if res.trades.empty:
        print("WARN: backtest produced 0 trades. Aborting.", file=sys.stderr)
        return 3

    print(f"[prop-fit] Backtest produced {len(res.trades):,} trades")

    trades_df = _build_trades_df(
        res.trades, point_value=instr.point_value, symbol=instr.symbol
    )

    print("[prop-fit] Building trade_paths from 1-min RTH bars...")
    trades_df, paths_df = _build_paths_df(trades_df, df_1min)
    print(
        f"[prop-fit] Path rows: {len(paths_df):,} "
        f"({len(paths_df) / max(len(trades_df), 1):.1f} bars/trade avg)"
    )

    # Post-shift invariant: min(bar_time per trade) >= entry_time per trade.
    # Validates that no path bar is pre-fill (the user-requested fix for
    # ambiguity #1 from the v1 export). If this fails the entry_time shift is
    # broken, the underlying data has a discontinuity, or paths slicing logic
    # regressed — block the run before producing invalid output.
    _assert_no_pre_fill_path_bars(trades_df, paths_df)

    # Reorder columns to contract canonical order (cosmetic, helps reviewers).
    trades_df = trades_df[_TRADE_COLS_ORDER]
    paths_df = paths_df[_PATH_COLS_ORDER]

    trades_path = out_dir / "trades.parquet"
    paths_path = out_dir / "trade_paths.parquet"
    trades_df.to_parquet(trades_path, index=False)
    paths_df.to_parquet(paths_path, index=False)
    print(f"[prop-fit] Saved trades -> {trades_path}")
    print(f"[prop-fit] Saved paths  -> {paths_path}")

    # Validate.
    if args.skip_validator:
        print("[prop-fit] --skip-validator: skipping validation step")
        validator_status = "SKIPPED"
    else:
        print("[prop-fit] Running validator (load_prop_fit_dataset) ...")
        try:
            result = _run_validator(trades_path, paths_path)
            if result is None:
                validator_status = "SKIPPED (no env)"
            else:
                n_t, n_p = result
                print(f"[prop-fit] OK  Validator passed: {n_t} trades, {n_p} paths")
                validator_status = "PASS"
        except Exception as exc:
            print(f"[prop-fit] FAIL  Validator: {exc}", file=sys.stderr)
            return 4

    # Final report.
    _print_summary(trades_df, paths_df, trades_path, paths_path, validator_status)
    return 0


# --- Report helpers ---------------------------------------------------------


def _print_summary(
    trades_df: pd.DataFrame,
    paths_df: pd.DataFrame,
    trades_path: Path,
    paths_path: Path,
    validator_status: str,
) -> None:
    date_min = trades_df["session_date"].min()
    date_max = trades_df["session_date"].max()
    bdays = pd.bdate_range(date_min, date_max)
    active_days = trades_df["session_date"].nunique()
    active_day_rate = active_days / max(len(bdays), 1)

    # Post-fill invariant spot-check (also asserted in main before save).
    entry_by_id = trades_df.set_index("trade_id")["entry_time"]
    min_bar_by_id = (
        paths_df.groupby("trade_id", sort=False)["bar_time"].min()
    )
    invariant_ok = (min_bar_by_id >= entry_by_id.loc[min_bar_by_id.index]).all()

    print()
    print("=" * 72)
    print("Phase E export -> prop-fit optimizer contract v1")
    print("=" * 72)
    print(f"Trades total          : {len(trades_df):,}")
    print(f"Path bars total       : {len(paths_df):,}")
    print(
        f"Avg bars per trade    : "
        f"{len(paths_df) / max(len(trades_df), 1):.1f}"
    )
    print(
        f"Period covered        : {date_min} -> {date_max} "
        f"({len(bdays):,} business days)"
    )
    print(
        f"Active days           : {active_days:,} "
        f"(active-day rate {active_day_rate:.1%})"
    )
    print(f"PnL net total         : ${trades_df['pnl'].sum():,.2f}")
    print(f"Commissions total     : ${trades_df['commission'].sum():,.2f}")
    print(f"Slippage total        : ${trades_df['slippage'].sum():,.2f}")
    print(f"Exit reason breakdown :")
    for reason, count in (
        trades_df["exit_reason"].value_counts().to_dict().items()
    ):
        print(f"  {reason:<14} : {count:>6}")
    print(f"Validator status      : {validator_status}")
    print(
        f"Invariant min(bar_time) >= entry_time : "
        f"{'OK' if invariant_ok else 'FAIL'}"
    )
    print()
    print(f"Output paths:")
    print(f"  {trades_path}")
    print(f"  {paths_path}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
