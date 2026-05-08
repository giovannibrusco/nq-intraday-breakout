import os
from datetime import time
from pathlib import Path

import pytest

from nqbreakout.prop.apex import (
    ApexAccount, ApexDrawdownTracker,
    consistency_ok, enough_trading_days,
    load_apex_accounts,
)

CONFIG_PATH = Path(__file__).parent.parent / "configs" / "apex_accounts.yaml"


def make_account(**overrides):
    base = dict(
        name="test",
        starting_balance=50000,
        profit_target=3000,
        trailing_threshold=2500,
        max_contracts_nq=6,
        max_contracts_mnq=60,
    )
    base.update(overrides)
    return ApexAccount(**base)


def test_load_apex_accounts_seven_tiers():
    accounts = load_apex_accounts(CONFIG_PATH)
    assert len(accounts) == 7
    names = [a.name for a in accounts]
    assert names == ["apex_25k", "apex_50k", "apex_75k", "apex_100k", "apex_150k", "apex_250k", "apex_300k"]
    for a in accounts:
        assert a.allow_overnight is False
        assert a.flatten_cutoff == time(16, 59)


def test_apex_account_is_frozen():
    acc = make_account()
    with pytest.raises(Exception):
        acc.starting_balance = 999


def test_trailing_floor_initial():
    acc = make_account()
    t = ApexDrawdownTracker(acc)
    assert t.floor == 50000 - 2500  # 47500


def test_trailing_floor_trails_until_threshold():
    acc = make_account()  # starting 50000, profit_target 3000, trailing 2500
    t = ApexDrawdownTracker(acc)
    t.update_eod(51000)
    assert t.floor == 48500  # 51000 - 2500
    t.update_eod(52000)
    assert t.floor == 49500  # 52000 - 2500
    t.update_eod(51500)  # pullback: floor stays
    assert t.floor == 49500
    assert not t.frozen


def test_trailing_floor_frozen_after_passing():
    acc = make_account()
    t = ApexDrawdownTracker(acc)
    t.update_eod(53000)  # hits 50000 + 3000
    assert t.frozen
    assert t.floor == 50000
    # further highs do not move the floor
    t.update_eod(55000)
    assert t.floor == 50000


def test_floor_never_decreases():
    acc = make_account()
    t = ApexDrawdownTracker(acc)
    t.update_eod(52000)
    assert t.floor == 49500
    t.update_eod(51000)  # equity drop
    assert t.floor == 49500


def test_violation_intraday_stopout():
    acc = make_account()
    t = ApexDrawdownTracker(acc)
    t.update_eod(52000)  # floor 49500
    assert t.violated(49400)
    assert not t.violated(49500)


def test_consistency_30pct_pass():
    pnl = [300, 300, 300, 300]  # even
    assert consistency_ok(pnl, 0.30)


def test_consistency_30pct_fail():
    pnl = [1000, 100, 100, 100]  # one day dominates
    assert not consistency_ok(pnl, 0.30)


def test_consistency_no_profitable_days():
    pnl = [-100, -50, -200]
    assert not consistency_ok(pnl, 0.30)


def test_min_days_gating():
    assert not enough_trading_days(5, 7)
    assert enough_trading_days(7, 7)
    assert enough_trading_days(10, 7)


def test_fee_per_rt_higher_than_ib():
    accounts = load_apex_accounts(CONFIG_PATH)
    for a in accounts:
        assert a.fee_per_rt_usd > 1.70  # IB baseline
