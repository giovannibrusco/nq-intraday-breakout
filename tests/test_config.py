"""Tests for ``nqbreakout.strategy.config``."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from nqbreakout.strategy.config import (
    InstrumentSpec,
    StrategyConfig,
    load_instrument_spec,
    load_strategy_config,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "strategy_default.yaml"


def test_load_strategy_config_reads_defaults() -> None:
    """``load_strategy_config`` returns the YAML values verbatim."""
    cfg = load_strategy_config(DEFAULT_CONFIG)

    assert isinstance(cfg, StrategyConfig)
    assert cfg.window_start == dt.time(8, 30)
    assert cfg.window_end == dt.time(10, 0)
    assert cfg.trade_start == dt.time(10, 0)
    assert cfg.trade_end == dt.time(14, 30)
    assert cfg.regular_start == dt.time(9, 30)
    assert cfg.regular_end == dt.time(16, 0)
    assert cfg.stop_points == 100.0
    assert cfg.target_points == 200.0
    assert cfg.max_trades_per_day == 2
    assert cfg.max_long_hold_days == 5
    assert cfg.short_intraday_only is True
    assert cfg.initial_capital == 1_000_000.0
    assert cfg.risk_pct == 0.01
    assert cfg.margin_per_contract == 23282.57
    assert cfg.margin_buffer == 0.15


def test_strategy_config_is_frozen() -> None:
    """``StrategyConfig`` is immutable; attribute assignment must raise."""
    cfg = StrategyConfig()
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        cfg.stop_points = 50.0  # type: ignore[misc]


def test_load_instrument_spec_nq_defaults() -> None:
    spec = load_instrument_spec("NQ")
    assert isinstance(spec, InstrumentSpec)
    assert spec.symbol == "NQ"
    assert spec.point_value == 20.0
    assert spec.tick_size == 0.25
    assert spec.tick_value == 5.0
    assert spec.pnl_scale == 1.0
    assert spec.position_scale == 1


def test_load_instrument_spec_mnq() -> None:
    spec = load_instrument_spec("mnq")  # case-insensitive
    assert spec.symbol == "MNQ"
    assert spec.point_value == 2.0
    assert spec.pnl_scale == 0.1
    assert spec.position_scale == 10


def test_load_instrument_spec_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_instrument_spec("ES")
