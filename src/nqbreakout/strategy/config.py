"""Strategy and instrument configuration.

Immutable dataclasses loaded from YAML files under ``configs/``. These are the
single source of truth for strategy parameters. Downstream modules
(``core.run_backtest``, ``filters.FilterEngine``, CLI scripts, notebooks)
consume these objects directly rather than reading YAML themselves.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FilterSpec:
    """Regime filter toggles and thresholds.

    Phase 4 ships the real thresholds. Each filter is individually toggleable
    and defaults to disabled, so that a default ``FilterSpec()`` yields the
    Phase 1 passthrough behavior and preserves the parity contract in
    ``core.run_backtest``.

    Backward-compat: the legacy placeholder field names ``gap_atr_max``,
    ``trend_ema_period``, ``volume_ratio_min`` are retained to avoid breaking
    YAML files that might reference them. They are not used by the Phase 4
    filter engine (which reads ``gap_atr_max_long`` / ``trend_ema_window`` /
    ``liquidity_min_ratio`` instead) but remain loadable.
    """

    enable_overnight_gap: bool = False
    enable_prior_range: bool = False
    enable_trend: bool = False
    enable_liquidity: bool = False

    # Overnight gap filter
    gap_pct_min_long: float = -0.003       # -0.30%
    gap_pct_max_short: float = 0.003       # +0.30%
    gap_atr_max_long: float = 1.5          # one-directional extremeness cap
    gap_atr_max_both: float = 2.0          # both-directional extremeness cap
    atr_window: int = 20

    # Prior day range filter
    range_percentile_max: float = 0.75
    range_window_days: int = 60

    # Trend filter
    trend_ema_window: int = 50
    trend_source: str = "NQ"               # "NQ" or "QQQ"

    # Liquidity filter
    liquidity_session_start: dt.time = dt.time(8, 30)
    liquidity_session_end: dt.time = dt.time(10, 0)
    liquidity_min_ratio: float = 0.5
    liquidity_median_window_days: int = 20

    # Legacy placeholder fields (Phase 0/1); preserved for backward compat.
    gap_atr_max: float = 2.0
    trend_ema_period: int = 50
    volume_ratio_min: float = 0.5


@dataclass(frozen=True)
class InstrumentSpec:
    """Contract specification (NQ or MNQ).

    ``pnl_scale`` and ``position_scale`` allow rescaling an NQ backtest to MNQ
    without re-running the bar loop: $1 PnL on NQ corresponds to $0.10 on MNQ,
    and one NQ contract is equivalent to ten MNQ contracts.
    """

    symbol: str = "NQ"
    point_value: float = 20.0
    tick_size: float = 0.25
    tick_value: float = 5.0
    pnl_scale: float = 1.0
    position_scale: int = 1

    @classmethod
    def from_yaml(cls, path: str | Path) -> InstrumentSpec:
        data = yaml.safe_load(Path(path).read_text())
        return cls(**data.get("instrument", {}))


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable strategy parameters.

    All time fields are US/Eastern, tz-naive - they are compared directly to
    ``pd.Timestamp.time()`` values on the 5-minute bars (which are assumed
    already in US/Eastern and tz-naive, as produced by the legacy loader).
    """

    # Session (US/Eastern, tz-naive)
    window_start: dt.time = dt.time(8, 30)
    window_end: dt.time = dt.time(10, 0)
    trade_start: dt.time = dt.time(10, 0)
    trade_end: dt.time = dt.time(14, 30)
    regular_start: dt.time = dt.time(9, 30)
    regular_end: dt.time = dt.time(16, 0)

    # Entry / exit
    stop_points: float = 100.0
    target_points: float = 200.0
    max_trades_per_day: int = 2
    max_long_hold_days: int = 5
    short_intraday_only: bool = True

    # Sizing
    initial_capital: float = 1_000_000.0
    risk_pct: float = 0.01
    margin_per_contract: float = 23282.57
    margin_buffer: float = 0.15
    max_position: int = 10**6

    # EOD force-flatten (Phase 3).
    # When True, any open trade is force-closed at the bar whose time-of-day
    # reaches eod_cutoff_time. Default False preserves Phase 1 parity exactly.
    flatten_at_eod: bool = False
    eod_cutoff_time: dt.time = dt.time(16, 0)  # 16:00 ET default; Apex sets 16:59

    # Filters (Phase 4)
    filter_spec: FilterSpec = field(default_factory=FilterSpec)

    # Phase 7: long-only mode.
    # When True, all short entry logic is skipped. Phase 6 found shorts have
    # mean PnL -$296/trade vs longs +$3,836/trade -- zero edge. Default False
    # preserves all prior behavior; Phase 1 parity remains intact.
    long_only: bool = False

    # Phase E rev: Apex Daily Loss Limit applied at backtest level.
    # When > 0.0, run_backtest tracks cumulative day PnL (realized + unrealized
    # at intraday extreme) and force-flattens open trade + blocks new entries
    # for the rest of the day if cum day PnL <= -daily_loss_limit. Default 0.0
    # disables DLL → preserves Phase 0-9c parity. Pass live-nominal $ amount
    # (e.g. $500 for Apex 25K_eod PA L0).
    daily_loss_limit: float = 0.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> StrategyConfig:
        """Parse YAML with top-level ``strategy:`` key.

        ISO-formatted time strings are converted to ``dt.time`` instances.
        Unknown keys raise ``TypeError`` via the dataclass constructor.
        A nested ``filter_spec:`` block is deserialized into a ``FilterSpec``.
        """
        data = yaml.safe_load(Path(path).read_text())
        strat = dict(data.get("strategy", {}))
        time_keys = (
            "window_start",
            "window_end",
            "trade_start",
            "trade_end",
            "regular_start",
            "regular_end",
            "eod_cutoff_time",
        )
        for k in time_keys:
            if k in strat and isinstance(strat[k], str):
                strat[k] = dt.time.fromisoformat(strat[k])

        # Nested filter_spec: decode time-valued fields then build the dataclass.
        fspec = strat.pop("filter_spec", None)
        if isinstance(fspec, dict):
            fspec = dict(fspec)
            filter_time_keys = ("liquidity_session_start", "liquidity_session_end")
            for k in filter_time_keys:
                if k in fspec and isinstance(fspec[k], str):
                    fspec[k] = dt.time.fromisoformat(fspec[k])
            strat["filter_spec"] = FilterSpec(**fspec)

        return cls(**strat)

    def to_dict(self) -> dict[str, Any]:
        """JSON/YAML serializable representation (times as ISO strings)."""
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, dt.time):
                d[k] = v.isoformat()
        return d


# Module-level instrument registry. Phase 1 hardcodes NQ and MNQ;
# subsequent phases can load from YAML if multiple specs are needed.
_INSTRUMENTS: dict[str, InstrumentSpec] = {
    "NQ": InstrumentSpec(),
    "MNQ": InstrumentSpec(
        symbol="MNQ",
        point_value=2.0,
        tick_size=0.25,
        tick_value=0.5,
        pnl_scale=0.1,
        position_scale=10,
    ),
}

NQ_DEFAULT = _INSTRUMENTS["NQ"]
MNQ_DEFAULT = _INSTRUMENTS["MNQ"]


def load_strategy_config(path: str | Path) -> StrategyConfig:
    """Load strategy config from YAML. Thin wrapper around ``StrategyConfig.from_yaml``."""
    return StrategyConfig.from_yaml(path)


def load_instrument_spec(name: str = "NQ") -> InstrumentSpec:
    """Return the registered ``InstrumentSpec`` for ``name`` (case-insensitive)."""
    key = name.strip().upper()
    if key not in _INSTRUMENTS:
        raise KeyError(f"Unknown instrument: {name!r}. Known: {sorted(_INSTRUMENTS)}")
    return _INSTRUMENTS[key]
