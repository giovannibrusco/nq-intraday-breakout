"""Phase 10C: Dual-geometry test for products with divergent best_TP signals.

For each candidate product, runs lifecycle MC with eval_pnl from one TP series
(maximizing pass rate) and PA_pnl from a different TP series (maximizing payout
post-pass). Compares to single-geometry baselines.

Phase 10A frontier identified 3 products with dual signal:
  - apex_100k_intraday: best_pass_TP=168, best_net_TP=115
  - apex_25k_eod:       best_pass_TP=82,  best_net_TP=115
  - apex_150k_eod:      best_pass_TP=245, best_net_TP=115

Per-product test: dual = (eval_TP=best_pass, pa_TP=best_net)
Compare to: single_pass = both phases use best_pass_TP
            single_net  = both phases use best_net_TP

Output:
  - reports/phase10c_dual_geometry/results.csv
  - reports/phase10c_dual_geometry/VERDICT_10c.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import run_lifecycle_sweep

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, summarize  # noqa: E402

# (product_name, eval_TP, pa_TP) — from Phase 10A frontier
DUAL_CANDIDATES = [
    ("apex_100k_intraday", 168, 115),
    ("apex_25k_eod",       82,  115),
    ("apex_150k_eod",      245, 115),
]


def load_pnl_for_tp(sweep_dir: Path, tp: int, tier: str) -> pd.Series:
    fname, factor = TIER_TO_PARQUET[tier]
    path = sweep_dir / f"daily_pnl_tp{tp:03d}" / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing TP={tp} parquet for tier {tier}: {path}")
    return pd.read_parquet(path)["daily_pnl"] * factor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--sweep-dir", default="reports/phase10a_mfe_tp")
    parser.add_argument("--out-dir", default="reports/phase10c_dual_geometry")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    args = parser.parse_args()

    products_all = {p.name: p for p in load_apex_products(args.products)}
    sweep_dir = Path(args.sweep_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, eval_tp, pa_tp in DUAL_CANDIDATES:
        product = products_all[name]
        tier = product.eval.tier

        pnl_eval = load_pnl_for_tp(sweep_dir, eval_tp, tier)
        pnl_pa = load_pnl_for_tp(sweep_dir, pa_tp, tier)

        print(f"\n[Phase 10C] {name}: eval_TP={eval_tp}, pa_TP={pa_tp}")

        # 1. DUAL: eval_TP for eval, pa_TP for PA
        df_dual = run_lifecycle_sweep(
            pnl_per_mini=pnl_eval,
            product=product,
            eval_contracts_mini=args.eval_contracts_mini,
            n_sims=args.sims,
            pa_max_days=args.pa_max_days,
            block_size=args.block_size,
            seed=args.seed,
            pnl_per_mini_pa=pnl_pa,
        )
        df_dual.to_parquet(out_dir / f"{name}_dual.parquet")
        s_dual = summarize(df_dual, product)

        # 2. SINGLE_PASS: both phases use eval_TP (max pass)
        df_pass = run_lifecycle_sweep(
            pnl_per_mini=pnl_eval, product=product,
            eval_contracts_mini=args.eval_contracts_mini,
            n_sims=args.sims, pa_max_days=args.pa_max_days,
            block_size=args.block_size, seed=args.seed,
        )
        s_pass = summarize(df_pass, product)

        # 3. SINGLE_NET: both phases use pa_TP (max E[net])
        df_net = run_lifecycle_sweep(
            pnl_per_mini=pnl_pa, product=product,
            eval_contracts_mini=args.eval_contracts_mini,
            n_sims=args.sims, pa_max_days=args.pa_max_days,
            block_size=args.block_size, seed=args.seed,
        )
        s_net = summarize(df_net, product)

        for label, summary, eval_used, pa_used in [
            ("dual", s_dual, eval_tp, pa_tp),
            ("single_pass", s_pass, eval_tp, eval_tp),
            ("single_net", s_net, pa_tp, pa_tp),
        ]:
            rows.append({
                "product": name,
                "config": label,
                "eval_TP": eval_used,
                "pa_TP": pa_used,
                "pass_rate": round(summary["pass_rate"], 4),
                "E_net_promo": round(summary["expected_net_per_cycle_promo"], 0),
                "annualized_promo": round(summary["annualized_net_promo"], 0),
                "pct_max_payouts": round(summary.get("pct_max_payouts", 0.0), 3),
                "pct_trail_violated": round(summary.get("pct_trailing_violated", 0.0), 3),
            })
            print(
                f"  {label:12s} eval_TP={eval_used:3d} pa_TP={pa_used:3d} | "
                f"pass={summary['pass_rate']:.3f} "
                f"E[net]=${summary['expected_net_per_cycle_promo']:>6,.0f} "
                f"ann=${summary['annualized_net_promo']:>7,.0f}"
            )

    res = pd.DataFrame(rows)
    res_path = out_dir / "results.csv"
    res.to_csv(res_path, index=False)
    print(f"\n[Phase 10C] Results -> {res_path}")
    print("\n" + res.to_string(index=False))

    # Compute uplift dual vs best single
    print("\n[Phase 10C] Dual uplift vs best single:")
    uplift_rows = []
    for name, eval_tp, pa_tp in DUAL_CANDIDATES:
        sub = res[res["product"] == name]
        dual = sub[sub["config"] == "dual"].iloc[0]
        sp = sub[sub["config"] == "single_pass"].iloc[0]
        sn = sub[sub["config"] == "single_net"].iloc[0]
        best_single = max(sp["E_net_promo"], sn["E_net_promo"])
        dual_uplift = dual["E_net_promo"] - best_single
        dual_uplift_pct = (dual["E_net_promo"] / best_single - 1) * 100 if best_single else 0
        uplift_rows.append({
            "product": name,
            "best_single_E_net": best_single,
            "dual_E_net": dual["E_net_promo"],
            "dual_uplift": dual_uplift,
            "dual_uplift_pct": round(dual_uplift_pct, 1),
        })
        print(
            f"  {name}: best_single=${best_single:,.0f}, dual=${dual['E_net_promo']:,.0f}, "
            f"uplift=${dual_uplift:+,.0f} ({dual_uplift_pct:+.1f}%)"
        )

    uplift = pd.DataFrame(uplift_rows)
    uplift.to_csv(out_dir / "dual_uplift.csv", index=False)


if __name__ == "__main__":
    main()
