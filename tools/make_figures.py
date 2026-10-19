"""Regenerate the README figures from the real data.

Usage::

    python tools/make_figures.py --nq Data/nq-10y-1min.csv --qqq Data/QQQ_daily.csv

Writes ``docs/images/equity_curve.png`` and ``docs/images/drawdown.png``
for the headline configuration: corrected conventions, stop-fill entries,
bars read as end-stamped (the MultiCharts export convention).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import matplotlib

matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from nq_breakout import Config, align_benchmark, load_nq_1min, load_qqq, resample_5min, run_backtest

SURFACE = '#fcfcfb'
INK = '#2b2b29'
MUTED = '#7a7974'
GRID = '#e4e3dd'
BLUE, ORANGE, AQUA = '#2a78d6', '#eb6834', '#1baf7a'


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))


def _end_labels(ax, series, min_gap):
    """Direct labels at the right edge, nudged apart so they never overlap."""
    items = sorted(((s.iloc[-1], name, color) for name, s, color in series), reverse=True)
    placed = []
    for y, name, color in items:
        if placed and placed[-1] - y < min_gap:
            y = placed[-1] - min_gap
        placed.append(y)
        x = series[0][1].index[-1]
        # A colored swatch carries identity; the name itself stays in ink.
        ax.annotate('—', xy=(x, y), xytext=(8, 0), textcoords='offset points',
                    va='center', fontsize=12, color=color, fontweight='bold',
                    annotation_clip=False)
        ax.annotate(name, xy=(x, y), xytext=(24, 0), textcoords='offset points',
                    va='center', fontsize=11, color=INK, annotation_clip=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--nq', type=Path, required=True)
    ap.add_argument('--qqq', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=ROOT / 'docs' / 'images')
    args = ap.parse_args()

    bars = resample_5min(load_nq_1min(args.nq, stamped_at='end'))
    one = run_backtest(bars, Config(risk_pct=0.01)).equity
    two = run_backtest(bars, Config(risk_pct=0.02)).equity
    qqq = align_benchmark(load_qqq(args.qqq), one.index, Config().initial_capital)
    capital = Config().initial_capital
    args.out.mkdir(parents=True, exist_ok=True)

    # Cumulative PnL.
    pnl = [('NQ 2% risk', (two - capital) / 1e6, ORANGE),
           ('QQQ buy & hold', (qqq - capital) / 1e6, AQUA),
           ('NQ 1% risk', (one - capital) / 1e6, BLUE)]
    fig, ax = plt.subplots(figsize=(11, 4.8), facecolor=SURFACE)
    _style(ax)
    for name, s, color in pnl:
        ax.plot(s.index, s.values, color=color, linewidth=1.8, label=name)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_xlim(one.index[0], one.index[-1])
    ax.set_ylabel('Cumulative PnL ($M)', color=MUTED)
    ax.set_title('Daily mark-to-market PnL, stop-entry fills, net of costs (2015–2025)',
                 loc='left', color=INK, fontsize=13)
    ax.legend(loc='upper left', frameon=False, labelcolor=INK)
    _end_labels(ax, pnl, min_gap=0.75)
    fig.subplots_adjust(left=0.07, right=0.84, top=0.9, bottom=0.08)
    fig.savefig(args.out / 'equity_curve.png', dpi=150, facecolor=SURFACE)
    plt.close(fig)

    # Drawdown from running peak.
    dd = [('NQ 1% risk', (one / one.cummax() - 1) * 100, BLUE),
          ('QQQ buy & hold', (qqq / qqq.cummax() - 1) * 100, AQUA)]
    fig, ax = plt.subplots(figsize=(11, 4.0), facecolor=SURFACE)
    _style(ax)
    for name, s, color in dd:
        ax.plot(s.index, s.values, color=color, linewidth=1.4, label=name)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_xlim(one.index[0], one.index[-1])
    ax.set_ylabel('Drawdown (%)', color=MUTED)
    ax.set_title('Drawdown from running peak, daily mark-to-market equity',
                 loc='left', color=INK, fontsize=13)
    ax.legend(loc='lower left', frameon=False, labelcolor=INK)
    _end_labels(ax, dd, min_gap=3.0)
    fig.subplots_adjust(left=0.07, right=0.84, top=0.88, bottom=0.09)
    fig.savefig(args.out / 'drawdown.png', dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f'Wrote {args.out / "equity_curve.png"} and {args.out / "drawdown.png"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
