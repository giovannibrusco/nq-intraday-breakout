import datetime as dt
import unittest

import numpy as np

from topstep_montecarlo import (
    DayProfile,
    RuleSet,
    SimConfig,
    position_size,
    simulate_many,
    simulate_one_run,
)


class TopstepMonteCarloTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule_topstepx = RuleSet(
            name="topstepx_no_dll",
            profit_target=3000.0,
            max_position=5,
            max_loss_limit=2000.0,
            daily_loss_limit=None,
            consistency_threshold=0.5,
        )
        self.rule_legacy = RuleSet(
            name="legacy_with_dll",
            profit_target=3000.0,
            max_position=5,
            max_loss_limit=2000.0,
            daily_loss_limit=-1000.0,
            consistency_threshold=0.5,
        )

    def test_pass_certain_profile(self) -> None:
        profiles = [
            DayProfile(
                date=dt.date(2025, 1, 1),
                pnl_per_contract_list=[1000.0],
                stop_dollars_per_contract_list=[500.0],
            )
        ]
        cfg = SimConfig(seed=42, max_sim_days=10, conservative_intraday=True)
        res = simulate_one_run(1, self.rule_topstepx, cfg, profiles, np.random.default_rng(42))
        self.assertEqual(res.status, "pass")
        self.assertEqual(res.days, 3)

    def test_fail_mll(self) -> None:
        profiles = [
            DayProfile(
                date=dt.date(2025, 1, 1),
                pnl_per_contract_list=[-2500.0],
                stop_dollars_per_contract_list=[2500.0],
            )
        ]
        cfg = SimConfig(seed=42, max_sim_days=10, conservative_intraday=True)
        res = simulate_one_run(1, self.rule_topstepx, cfg, profiles, np.random.default_rng(42))
        self.assertEqual(res.status, "fail")
        self.assertTrue(res.reason.startswith("mll_"))

    def test_fail_dll_legacy_only(self) -> None:
        profiles = [
            DayProfile(
                date=dt.date(2025, 1, 1),
                pnl_per_contract_list=[-1200.0],
                stop_dollars_per_contract_list=[1200.0],
            )
        ]
        cfg = SimConfig(seed=42, max_sim_days=1, conservative_intraday=True)

        legacy_res = simulate_one_run(1, self.rule_legacy, cfg, profiles, np.random.default_rng(42))
        topstepx_res = simulate_one_run(1, self.rule_topstepx, cfg, profiles, np.random.default_rng(42))

        self.assertEqual(legacy_res.status, "fail")
        self.assertTrue(legacy_res.reason.startswith("dll_"))
        self.assertEqual(topstepx_res.status, "timeout")

    def test_fail_consistency(self) -> None:
        profiles = [
            DayProfile(
                date=dt.date(2025, 1, 1),
                pnl_per_contract_list=[3000.0],
                stop_dollars_per_contract_list=[500.0],
            )
        ]
        cfg = SimConfig(seed=42, max_sim_days=2, conservative_intraday=True)
        res = simulate_one_run(1, self.rule_topstepx, cfg, profiles, np.random.default_rng(42))
        self.assertEqual(res.status, "timeout")
        self.assertGreaterEqual(res.final_balance, 53000.0)

    def test_sizing_cap(self) -> None:
        contracts = position_size(
            equity=1_000_000.0,
            stop_dollars_per_contract=100.0,
            risk_pct=0.10,
            margin_per_contract=1.0,
            margin_buffer=0.0,
            max_position=5,
        )
        self.assertEqual(contracts, 5)

    def test_reproducibility(self) -> None:
        profiles = [
            DayProfile(
                date=dt.date(2025, 1, 1),
                pnl_per_contract_list=[400.0],
                stop_dollars_per_contract_list=[400.0],
            ),
            DayProfile(
                date=dt.date(2025, 1, 2),
                pnl_per_contract_list=[-300.0],
                stop_dollars_per_contract_list=[400.0],
            ),
        ]
        cfg = SimConfig(n_sims=50, seed=123, max_sim_days=20, conservative_intraday=True)

        r1 = simulate_many(self.rule_topstepx, cfg, profiles, seed_offset=0)
        r2 = simulate_many(self.rule_topstepx, cfg, profiles, seed_offset=0)

        t1 = [(x.status, x.reason, x.days, round(x.final_balance, 8)) for x in r1]
        t2 = [(x.status, x.reason, x.days, round(x.final_balance, 8)) for x in r2]
        self.assertEqual(t1, t2)


if __name__ == "__main__":
    unittest.main()
