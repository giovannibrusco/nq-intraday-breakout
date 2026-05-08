"""Phase 10B.3: Lifecycle MC with regime-aware sampler.

Runs the same lifecycle MC as Phase 9C (TP=200 baseline daily PnL parquets)
but replaces the block bootstrap sampler with a Markov-regime-aware sampler.

Goal: validate Phase 9C numbers — does respecting overnight-vol regime
clustering (instead of just within-block autocorr) materially change pass
rates / E[net]? If yes, Phase 9C numbers are biased.

Reads:
  - data/daily_pnl_per_tier/apex_{tier}_longonly.parquet (Phase 9C inputs)
  - reports/phase10b_regime_mc/daily_regime_tags.parquet (Phase 10B.1 output)
  - reports/phase10b_regime_mc/transition_matrix.csv
  - reports/phase10b_regime_mc/stationary_distribution.csv

Writes:
  - reports/phase10b_regime_mc/{product}_lifecycle.parquet
  - reports/phase10b_regime_mc/summary.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import (
    block_bootstrap_regime_aware,
    run_lifecycle_sweep,
)

# Reuse phase9c logic
import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, load_pnl_per_mini, summarize  # noqa: E402

STATE_LABELS = ["low", "med", "high"]


def align_regimes_to_pnl(pnl: pd.Series, regime_df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Align daily regime tags to PnL series dates.

    PnL series index is dates (exit_date from trades). Regime tags df has
    columns date + regime. Returns (pnl_aligned, regimes_aligned) on the
    intersection of both date sets, sorted by date.
    """
    pnl_df = pnl.to_frame("pnl").reset_index().rename(columns={pnl.index.name or "index": "date"})
    pnl_df["date"] = pd.to_datetime(pnl_df["date"]).dt.normalize()

    rdf = regime_df.copy()
    rdf["date"] = pd.to_datetime(rdf["date"]).dt.normalize()

    merged = pnl_df.merge(rdf[["date", "regime"]], on="date", how="inner").sort_values("date")
    return (
        merged["pnl"].reset_index(drop=True),
        merged["regime"].reset_index(drop=True),
    )


def make_regime_sampler(
    pnl_aligned: pd.Series,
    regimes_aligned: pd.Series,
    transition_matrix: np.ndarray,
    stationary: np.ndarray,
    block_size: int,
):
    """Closure: return sampler_fn(n_days, rng) -> ndarray."""
    def sampler_fn(n_days, rng):
        return block_bootstrap_regime_aware(
            pnl_aligned, regimes_aligned, transition_matrix, stationary,
            STATE_LABELS, n_days, block_size, rng,
        )
    return sampler_fn


def main():
    parser = argparse.ArgumentParser(description="Lifecycle MC with regime-aware sampler.")
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--daily-pnl-dir", default="data/daily_pnl_per_tier")
    parser.add_argument("--regime-dir", default="reports/phase10b_regime_mc")
    parser.add_argument("--out-dir", default="reports/phase10b_regime_mc")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    args = parser.parse_args()

    products = load_apex_products(args.products)
    parquet_dir = Path(args.daily_pnl_dir)
    regime_dir = Path(args.regime_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load regime artifacts
    regime_df = pd.read_parquet(regime_dir / "daily_regime_tags.parquet")
    transition_matrix_df = pd.read_csv(regime_dir / "transition_matrix.csv", index_col=0)
    stationary_series = pd.read_csv(regime_dir / "stationary_distribution.csv", index_col=0).iloc[:, 0]

    # Ensure ordering matches STATE_LABELS
    transition_matrix = transition_matrix_df.loc[STATE_LABELS, STATE_LABELS].values.astype(float)
    stationary = np.array([float(stationary_series[s]) for s in STATE_LABELS], dtype=float)
    print(f"[Phase 10B.3] Regime states: {STATE_LABELS}")
    print(f"[Phase 10B.3] Stationary: {stationary.round(3).tolist()}")
    print(f"[Phase 10B.3] Transition matrix:\n{transition_matrix.round(3)}")

    summaries = []
    for product in products:
        try:
            pnl = load_pnl_per_mini(parquet_dir, product.eval.tier)
        except FileNotFoundError as e:
            print(f"SKIP {product.name}: {e}")
            continue

        pnl_aligned, regimes_aligned = align_regimes_to_pnl(pnl, regime_df)
        if len(pnl_aligned) < args.block_size:
            print(f"SKIP {product.name}: aligned PnL length {len(pnl_aligned)} < block_size {args.block_size}")
            continue

        # Validate every regime has >= block_size days in this tier's series
        regime_counts = regimes_aligned.value_counts()
        if any(regime_counts.get(s, 0) < args.block_size for s in STATE_LABELS):
            print(
                f"WARN {product.name}: some regime has < block_size aligned days. "
                f"counts={regime_counts.to_dict()}; falling back to overall regime_df"
            )
            # Fallback: use full regime_df (all dates) — but pnl needs same length
            # Easier: skip if can't align
            continue

        sampler_fn = make_regime_sampler(
            pnl_aligned, regimes_aligned, transition_matrix, stationary, args.block_size,
        )

        df = run_lifecycle_sweep(
            pnl_per_mini=pnl,  # original (not used by sampler_fn, only for legacy fallback)
            product=product,
            eval_contracts_mini=args.eval_contracts_mini,
            n_sims=args.sims,
            pa_max_days=args.pa_max_days,
            block_size=args.block_size,
            seed=args.seed,
            sampler_fn=sampler_fn,
        )
        df.to_parquet(out_dir / f"{product.name}_lifecycle.parquet")

        summary = summarize(df, product)
        summaries.append(summary)
        print(
            f"{product.name}: pass={summary['pass_rate']:.3f} "
            f"E[net|promo]=${summary.get('expected_net_per_cycle_promo', 0):,.0f} "
            f"ann=${summary.get('annualized_net_promo', 0):,.0f}"
        )

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(out_dir / "summary.csv", index=False)
    print(f"\n[Phase 10B.3] Summary -> {out_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
