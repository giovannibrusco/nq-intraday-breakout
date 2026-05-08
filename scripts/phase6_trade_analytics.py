"""Phase 6 - Trade-Level Analytics and Regime Breakdown.

Esegue l'analisi completa del trade lifecycle:
  1. Carica i dati NQ e riesegue il backtest baseline con MAE/MFE attivi
  2. Calcola le daily features (regime)
  3. Analisi per regime: gap_atr, range_pctile, trend_sign, vol_ratio
  4. MAE/MFE distributions per direzione e exit_reason
  5. Recheck del filtro prior_range per bin di range_pctile
  6. Scrive reports/phase6_trade_analytics*.csv e reports/phase6_mae_mfe_scatter.png
  7. Scrive reports/phase6_analytics.md con sommario esecutivo

Uso::
    python scripts/phase6_trade_analytics.py
    python scripts/phase6_trade_analytics.py --data data/nq_1min.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.stats.metrics import calmar, max_drawdown_pct, profit_factor, sharpe, sortino, win_rate
from nqbreakout.stats.trade_analytics import (
    add_regime_columns,
    edge_per_direction,
    exit_reason_breakdown,
    mae_mfe_distribution,
    per_regime_breakdown,
)
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest
from nqbreakout.strategy.filters import compute_daily_features


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 6: trade-level analytics")
    p.add_argument(
        "--data",
        type=str,
        default="data/nq_1min.parquet",
        help="Path to NQ 1-min parquet (relative to repo root).",
    )
    p.add_argument(
        "--config",
        type=str,
        default="configs/strategy_default.yaml",
        help="Strategy config YAML.",
    )
    p.add_argument(
        "--reports-dir",
        type=str,
        default="reports",
        help="Output directory for reports.",
    )
    p.add_argument("--seed", type=int, default=42, help="RNG seed (default 42).")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _daily_pnl_from_equity(equity: pd.Series, initial: float) -> pd.Series:
    """Reconstruct daily PnL from equity series."""
    if equity.empty:
        return pd.Series(dtype=float)
    eq = equity.sort_index().values.astype(float)
    prev = initial
    vals = []
    for v in eq:
        vals.append(v - prev)
        prev = v
    return pd.Series(vals, dtype=float)


def _regime_metrics(sub: pd.DataFrame, initial: float = 1_000_000.0) -> dict:
    """Compute trade-level metrics for a subset of trades."""
    if sub.empty:
        return {
            "count": 0, "total_pnl": 0.0, "mean_pnl": float("nan"),
            "win_rate": float("nan"), "sharpe_per_trade": float("nan"),
        }
    pnl = sub["pnl"].dropna()
    n = len(pnl)
    if n == 0:
        return {
            "count": 0, "total_pnl": 0.0, "mean_pnl": float("nan"),
            "win_rate": float("nan"), "sharpe_per_trade": float("nan"),
        }
    spt = float("nan")
    if n >= 2:
        std = float(pnl.std(ddof=1))
        spt = float(pnl.mean() / std) if std > 0 else float("nan")
    return {
        "count": n,
        "total_pnl": float(pnl.sum()),
        "mean_pnl": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "sharpe_per_trade": spt,
    }


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def _plot_mae_mfe_scatter(trades: pd.DataFrame, out_path: Path) -> None:
    """Scatter plot: MAE (x) vs MFE (y), colored by exit_reason."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  [skip scatter: matplotlib not available]")
        return

    if trades.empty or "mae_points" not in trades.columns:
        print("  [skip scatter: no mae/mfe data]")
        return

    reasons = trades["exit_reason"].unique() if "exit_reason" in trades.columns else ["unknown"]
    color_map = {}
    colors = plt.cm.Set1.colors  # type: ignore[attr-defined]
    for i, r in enumerate(sorted(reasons)):
        color_map[r] = colors[i % len(colors)]

    fig, ax = plt.subplots(figsize=(8, 6))
    for reason in sorted(reasons):
        sub = trades[trades["exit_reason"] == reason] if "exit_reason" in trades.columns else trades
        ax.scatter(
            sub["mae_points"].fillna(0),
            sub["mfe_points"].fillna(0),
            c=[color_map[reason]],
            label=str(reason),
            alpha=0.5,
            s=20,
        )

    ax.set_xlabel("MAE (points)")
    ax.set_ylabel("MFE (points)")
    ax.set_title("MAE vs MFE per Trade (colored by exit reason)")
    ax.legend(title="Exit Reason", fontsize=8)
    ax.plot([0, ax.get_xlim()[1]], [0, ax.get_xlim()[1]], "k--", linewidth=0.5, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print(f"  Scatter plot saved: {out_path}")


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_md_report(
    reports_dir: Path,
    baseline_metrics: dict,
    regime_tables: dict,
    mae_mfe_dist: dict,
    direction_df: pd.DataFrame,
    exit_df: pd.DataFrame,
    prior_range_recheck: pd.DataFrame,
    n_trades: int,
    calmar_old_estimate: float,
) -> None:
    """Scrivi il report Phase 6 in italiano con tabelle in inglese."""
    lines = []
    a = lines.append

    a("# Phase 6 - Trade-Level Analytics and Regime Breakdown")
    a("")
    a("## Sommario Esecutivo")
    a("")
    a("Questa fase aggiunge due miglioramenti strutturali al motore di backtest:")
    a("1. Calcolo in-loop di MAE (Max Adverse Excursion) e MFE (Max Favorable Excursion)")
    a("   per ogni trade, sostituendo i placeholder a zero della Fase 1.")
    a("2. Analisi per regime: breakdown delle metriche per bin di gap_atr,")
    a("   range_pctile, trend_sign e vol_ratio, per identificare contesti in cui")
    a("   la strategia funziona meglio o peggio.")
    a("")
    a("**Bug fix Calmar**: la versione precedente moltiplicava il PnL medio giornaliero")
    a("in dollari per 252 e divideva per una piccola frazione, producendo valori")
    a("nell'ordine di milioni. La formula corretta e':")
    a("  ann_return = (final/initial)^(252/n_days) - 1, diviso max_drawdown_pct.")
    a(f"  Valore precedente stimato: ~{calmar_old_estimate:,.0f} (artefatto).")
    a(f"  Valore corretto (baseline): {baseline_metrics.get('calmar', float('nan')):.4f}.")
    a("")
    a("## 1. Metriche Baseline (Full History, con MAE/MFE attivi)")
    a("")
    a("| Metric | Value |")
    a("|--------|-------|")
    for k, v in baseline_metrics.items():
        if isinstance(v, float):
            a(f"| {k} | {v:.4f} |")
        else:
            a(f"| {k} | {v} |")
    a("")

    a("## 2. MAE/MFE Distribution")
    a("")
    mae_pct = mae_mfe_dist.get("mae_pct", {})
    mfe_pct = mae_mfe_dist.get("mfe_pct", {})
    a("| Percentile | MAE (pts) | MFE (pts) |")
    a("|------------|-----------|-----------|")
    for p in [10, 25, 50, 75, 90]:
        a(f"| {p}th | {mae_pct.get(p, float('nan')):.1f} | {mfe_pct.get(p, float('nan')):.1f} |")
    ratio_med = mae_mfe_dist.get("mae_mfe_ratio_median", float("nan"))
    a(f"\nMAE/MFE ratio (mediana): {ratio_med:.3f}")
    a("")

    a("### 2a. Per Direction")
    a("")
    a("| Direction | Count | MAE Median (pts) | MFE Median (pts) |")
    a("|-----------|-------|-----------------|-----------------|")
    for direction, vals in mae_mfe_dist.get("by_direction", {}).items():
        a(f"| {direction} | {vals['count']} | {vals['mae_median']:.1f} | {vals['mfe_median']:.1f} |")
    a("")

    a("## 3. Edge per Direction")
    a("")
    if not direction_df.empty:
        a("| Direction | Count | Total PnL | Mean PnL | Win Rate | Max DD |")
        a("|-----------|-------|-----------|----------|----------|--------|")
        for idx, row in direction_df.iterrows():
            a(f"| {idx} | {int(row['count'])} | ${row['total_pnl']:,.0f} | "
              f"${row['mean_pnl']:,.0f} | {row['win_rate']:.3f} | ${row['max_dd']:,.0f} |")
    a("")

    a("## 4. Exit Reason Breakdown")
    a("")
    if not exit_df.empty:
        a("| Exit Reason | Count | Total PnL | Mean PnL | Mean MAE (pts) | Mean MFE (pts) |")
        a("|-------------|-------|-----------|----------|----------------|----------------|")
        for idx, row in exit_df.iterrows():
            a(f"| {idx} | {int(row['count'])} | ${row['total_pnl']:,.0f} | "
              f"${row['mean_pnl']:,.0f} | {row['mean_mae']:.1f} | {row['mean_mfe']:.1f} |")
    a("")

    a("## 5. Per-Regime Breakdown")
    a("")
    for regime_label, rdf in regime_tables.items():
        a(f"### {regime_label}")
        a("")
        if rdf.empty:
            a("(nessun dato)")
        else:
            a("| Regime | Count | Mean PnL | Win Rate | Sharpe per Trade |")
            a("|--------|-------|----------|----------|-----------------|")
            for idx, row in rdf.iterrows():
                spt = f"{row['sharpe_per_trade']:.3f}" if not pd.isna(row["sharpe_per_trade"]) else "nan"
                a(f"| {idx} | {int(row['count'])} | ${row['mean_pnl']:,.0f} | "
                  f"{row['win_rate']:.3f} | {spt} |")
        a("")

    a("## 6. Prior Range Filter - Recheck per Bin")
    a("")
    a("La Fase 4 aveva identificato un potenziale vantaggio del filtro prior_range")
    a("(+0.15 Sharpe) ma con p-value=0.38 (non significativo). Qui analizziamo")
    a("se il breakdown per bin di range_pctile rivela un edge differenziato.")
    a("")
    if not prior_range_recheck.empty:
        a("| range_pctile_bin | Count | Mean PnL | Win Rate | Sharpe per Trade |")
        a("|-----------------|-------|----------|----------|-----------------|")
        for idx, row in prior_range_recheck.iterrows():
            spt = f"{row['sharpe_per_trade']:.3f}" if not pd.isna(row["sharpe_per_trade"]) else "nan"
            a(f"| {idx} | {int(row['count'])} | ${row['mean_pnl']:,.0f} | "
              f"{row['win_rate']:.3f} | {spt} |")
        a("")
        a("**Interpretazione:** Se 'low+mid' ha mean_pnl e Sharpe significativamente superiori")
        a("a 'high', il filtro prior_range (blocco dei giorni con range alto) potrebbe")
        a("aggiungere valore. In assenza di significativita' statistica rigorosa (bootstrap)")
        a("si raccomanda di non attivare il filtro in produzione senza ulteriore validazione OOS.")
    else:
        a("(dati insufficienti per il recheck)")
    a("")

    a("## 7. Conclusioni e Raccomandazioni")
    a("")
    a("1. **MAE/MFE attivi**: i valori sono ora calcolati barra per barra durante")
    a("   il backtest, non post-hoc. Questo permette analisi di stop placement.")
    a("   MFE mediano elevato rispetto al target suggerisce che il profitto viene")
    a("   lasciato sul tavolo; MAE mediano indica quanto spesso il trade entra in")
    a("   territorio negativo prima di uscire.")
    a("2. **Calmar corretto**: il valore ora e' comparabile con altri sistemi.")
    a("3. **Regime analysis**: verificare se i bin 'trend_sign=+1' vs '-1' mostrano")
    a("   divergenza nella win_rate -- se si, il filtro trend potrebbe essere attivato.")
    a("4. **Prior range recheck**: vedi tabella sopra. Decisione condizionale.")
    a("")
    a("---")
    a("*Generato da scripts/phase6_trade_analytics.py (Phase 6)*")

    out_path = reports_dir / "phase6_analytics.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Report markdown salvato: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()
    rng = np.random.default_rng(args.seed)
    reports_dir = REPO_ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("PHASE 6 -- TRADE-LEVEL ANALYTICS AND REGIME BREAKDOWN")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Step 0: Load data
    # -----------------------------------------------------------------------
    print("\n[0] Caricamento dati...")
    data_path = REPO_ROOT / args.data
    if not data_path.exists():
        print(f"ERRORE: file dati non trovato: {data_path}")
        sys.exit(1)

    df_1min = load_nq_1min(data_path)
    df_5min = resample_to_5min(df_1min)
    date_min = df_5min["date"].min().date()
    date_max = df_5min["date"].max().date()
    print(f"  Range dati: {date_min} -> {date_max}")
    print(f"  Barre 5-min: {len(df_5min):,}")

    config_path = REPO_ROOT / args.config
    config = load_strategy_config(config_path)
    instrument = load_instrument_spec("NQ")
    print(f"  Config: stop={config.stop_points}, target={config.target_points}")

    # -----------------------------------------------------------------------
    # Step 1: Baseline backtest (MAE/MFE now active)
    # -----------------------------------------------------------------------
    print("\n[1] Backtest baseline (MAE/MFE attivi)...")
    result = run_backtest(df_5min, config, instrument)
    trades = result.trades.copy()
    n_trades = len(trades)
    print(f"  Trade totali: {n_trades}")
    print(f"  PnL totale: ${result.meta['total_pnl']:,.0f}")

    if n_trades == 0:
        print("  ATTENZIONE: nessun trade generato. Verificare i dati e la configurazione.")
        sys.exit(0)

    # Compute baseline metrics
    equity_series = result.daily_equity.sort_index()
    equity_arr = pd.Series(equity_series.values.astype(float), dtype=float)
    initial = float(config.initial_capital)
    daily_pnl = _daily_pnl_from_equity(equity_series, initial)
    trade_pnl = trades["pnl"].astype(float)

    # Estimate old (buggy) Calmar for reporting
    ann_return_old = float(daily_pnl.mean()) * 252.0
    mdd_pct_val = max_drawdown_pct(equity_arr)
    calmar_old_estimate = ann_return_old / mdd_pct_val if mdd_pct_val > 0 else float("nan")

    from nqbreakout.stats.metrics import calmar as calmar_fn
    baseline_metrics = {
        "sharpe": sharpe(daily_pnl),
        "sortino": sortino(daily_pnl),
        "calmar": calmar_fn(daily_pnl, equity_arr),
        "max_dd_pct": mdd_pct_val,
        "profit_factor": profit_factor(trade_pnl),
        "win_rate": win_rate(trade_pnl),
        "total_pnl": float(trade_pnl.sum()),
        "n_trades": n_trades,
    }

    print(f"  Sharpe: {baseline_metrics['sharpe']:.4f}")
    print(f"  Calmar (corretto): {baseline_metrics['calmar']:.4f}")
    print(f"  Calmar (stimato vecchio bug): {calmar_old_estimate:,.1f}")
    print(f"  MaxDD%: {baseline_metrics['max_dd_pct']:.4f}")

    # MAE/MFE summary
    print(f"\n  MAE mediana: {trades['mae_points'].median():.1f} pts")
    print(f"  MFE mediana: {trades['mfe_points'].median():.1f} pts")
    print(f"  MAE p90: {trades['mae_points'].quantile(0.90):.1f} pts")
    print(f"  MFE p90: {trades['mfe_points'].quantile(0.90):.1f} pts")

    # -----------------------------------------------------------------------
    # Step 2: Compute daily features
    # -----------------------------------------------------------------------
    print("\n[2] Calcolo daily features (regime)...")
    daily_features = compute_daily_features(df_5min)
    print(f"  Feature date range: {daily_features.index.min()} -> {daily_features.index.max()}")

    # -----------------------------------------------------------------------
    # Step 3: Add regime columns to trades
    # -----------------------------------------------------------------------
    print("\n[3] Join regime features sui trade...")
    trades_rich = add_regime_columns(trades, daily_features)
    print(f"  Colonne aggiunte: gap_atr_bin, range_pctile_bin, trend_sign, vol_ratio_bin")

    # -----------------------------------------------------------------------
    # Step 4: Per-regime breakdown
    # -----------------------------------------------------------------------
    print("\n[4] Breakdown per regime...")
    regime_tables = {}

    for col, label in [
        ("gap_atr_bin", "Gap ATR Bin (low/mid/high)"),
        ("range_pctile_bin", "Prior Range Percentile Bin"),
        ("trend_sign", "Trend Sign (EMA slope)"),
        ("vol_ratio_bin", "Volume Ratio Bin"),
        ("exit_reason", "Exit Reason"),
        ("direction", "Direction (long/short)"),
    ]:
        if col in trades_rich.columns:
            rdf = per_regime_breakdown(trades_rich, col)
            regime_tables[label] = rdf
            print(f"\n  --- {label} ---")
            print(rdf[["count", "mean_pnl", "win_rate", "sharpe_per_trade"]].to_string())

    # -----------------------------------------------------------------------
    # Step 5: MAE/MFE distributions
    # -----------------------------------------------------------------------
    print("\n[5] MAE/MFE distributions...")
    mae_mfe_dist = mae_mfe_distribution(trades_rich)
    print("  MAE percentiles:", {p: f"{v:.1f}" for p, v in mae_mfe_dist["mae_pct"].items()})
    print("  MFE percentiles:", {p: f"{v:.1f}" for p, v in mae_mfe_dist["mfe_pct"].items()})

    # -----------------------------------------------------------------------
    # Step 6: Edge per direction + exit reason
    # -----------------------------------------------------------------------
    print("\n[6] Edge per direzione e exit reason...")
    direction_df = edge_per_direction(trades_rich)
    exit_df = exit_reason_breakdown(trades_rich)
    print("\n  Direction breakdown:")
    print(direction_df.to_string())
    print("\n  Exit reason breakdown:")
    print(exit_df.to_string())

    # -----------------------------------------------------------------------
    # Step 7: Prior range recheck
    # -----------------------------------------------------------------------
    print("\n[7] Prior range filter recheck per bin...")
    prior_range_recheck = pd.DataFrame()
    if "range_pctile_bin" in trades_rich.columns:
        prior_range_recheck = per_regime_breakdown(trades_rich, "range_pctile_bin")
        print(prior_range_recheck.to_string())

        # Decision guidance
        if len(prior_range_recheck) >= 2:
            if "low" in prior_range_recheck.index and "high" in prior_range_recheck.index:
                low_mean = prior_range_recheck.loc["low", "mean_pnl"]
                high_mean = prior_range_recheck.loc["high", "mean_pnl"]
                diff = low_mean - high_mean
                print(f"\n  Low-bin mean PnL: ${low_mean:,.0f}, High-bin mean PnL: ${high_mean:,.0f}")
                print(f"  Differenza low vs high: ${diff:,.0f}")
                if diff > 0:
                    print("  -> Low-range giorni outperformano high-range giorni.")
                    print("  -> Il filtro prior_range (blocco high) POTREBBE aggiungere valore.")
                    print("  -> Raccomandazione: verifica bootstrap p-value prima di attivare.")
                else:
                    print("  -> High-range giorni non peggiori di low-range.")
                    print("  -> Filtro prior_range sconsigliato in questa configurazione.")

    # -----------------------------------------------------------------------
    # Step 8: Write CSV outputs
    # -----------------------------------------------------------------------
    print("\n[8] Scrittura CSV...")

    # Main enriched trades
    trades_out = reports_dir / "phase6_trade_analytics_trades.csv"
    trades_rich.to_csv(trades_out, index=False)
    print(f"  Trade arricchiti: {trades_out}")

    # Per-regime tables concatenated
    all_regime_rows = []
    for label, rdf in regime_tables.items():
        rdf_copy = rdf.copy()
        rdf_copy["regime_type"] = label
        rdf_copy["regime_value"] = rdf_copy.index.astype(str)
        all_regime_rows.append(rdf_copy.reset_index(drop=True))
    if all_regime_rows:
        combined_regime = pd.concat(all_regime_rows, ignore_index=True)
        regime_out = reports_dir / "phase6_trade_analytics_regime.csv"
        combined_regime.to_csv(regime_out, index=False)
        print(f"  Regime breakdown: {regime_out}")

    # MAE/MFE summary CSV
    mae_mfe_rows = []
    for p in [10, 25, 50, 75, 90]:
        mae_mfe_rows.append({
            "percentile": p,
            "mae_points": mae_mfe_dist["mae_pct"].get(p, float("nan")),
            "mfe_points": mae_mfe_dist["mfe_pct"].get(p, float("nan")),
        })
    mae_mfe_df = pd.DataFrame(mae_mfe_rows)
    mae_mfe_out = reports_dir / "phase6_mae_mfe_distribution.csv"
    mae_mfe_df.to_csv(mae_mfe_out, index=False)
    print(f"  MAE/MFE distribution: {mae_mfe_out}")

    # -----------------------------------------------------------------------
    # Step 9: Scatter plot
    # -----------------------------------------------------------------------
    print("\n[9] Generazione scatter plot MAE vs MFE...")
    _plot_mae_mfe_scatter(trades_rich, reports_dir / "phase6_mae_mfe_scatter.png")

    # -----------------------------------------------------------------------
    # Step 10: Markdown report
    # -----------------------------------------------------------------------
    print("\n[10] Scrittura report markdown...")
    _write_md_report(
        reports_dir=reports_dir,
        baseline_metrics=baseline_metrics,
        regime_tables=regime_tables,
        mae_mfe_dist=mae_mfe_dist,
        direction_df=direction_df,
        exit_df=exit_df,
        prior_range_recheck=prior_range_recheck,
        n_trades=n_trades,
        calmar_old_estimate=calmar_old_estimate,
    )

    print("\n" + "=" * 70)
    print("Phase 6 completo.")
    print("=" * 70)


if __name__ == "__main__":
    main()
