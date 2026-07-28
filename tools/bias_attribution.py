"""Attribute the difference between the legacy and corrected result sets.

The repository has two published result sets, produced by two different
sets of simulation conventions. This script runs the same bars under both,
then flips one convention at a time to isolate what each is worth.

Usage
-----
Synthetic bars (no data files needed, deterministic)::

    python tools/bias_attribution.py

Real data (reproduces the published numbers)::

    python tools/bias_attribution.py --nq Data/nq-10y-1min.csv --qqq Data/QQQ_daily.csv

The real-data run is the one that matters: only it can confirm whether
the legacy path reproduces Sharpe 0.95 / $3,287,392 / 1,728 trades and the
corrected path reproduces Sharpe 1.02 / $2,800,810 / 1,725 trades.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from nq_breakout import (LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, Config, SimFlags,
                         compute_metrics, legacy_equity_curve, load_nq_1min,
                         load_qqq, pad_to_calendar_days, replace, resample_5min,
                         run_backtest, trade_pnl_tstat)

# Flags flipped one at a time, from legacy toward corrected.
STEPS = [
    ('cost model (slippage x2 + $2.50/side)', 'config'),
    ('gap_aware_fills', 'flag'),
    ('scan_overnight_bars', 'flag'),
    ('exclusive_window_end', 'flag'),
    ('block_last_bar_entry', 'flag'),
    ('max_hold_exit_at_close', 'flag'),
    ('cme_trading_day', 'flag'),
    ('min_one_contract', 'flag'),
    ('require_prev_session', 'flag'),
    ('daily_mark_to_market', 'flag'),
    ('entry_mode stop', 'config'),
    ('drop calendar padding', 'equity'),
]


def summarise(result, pad: bool) -> dict:
    if pad and not result.flags.daily_mark_to_market:
        # Exactly the notebook's construction: exit-day steps spread over
        # every calendar day, seeded with initial capital.
        equity = legacy_equity_curve(result.trades, result.config.initial_capital)
    elif pad:
        # Marked to market, but still on a calendar-day grid, so the
        # padding step below isolates padding alone.
        equity = pad_to_calendar_days(result.equity)
    else:
        equity = result.equity
    m = compute_metrics(equity)
    t = trade_pnl_tstat(result.trades)
    return {
        'trades': len(result.trades),
        'net_pnl': result.trades['pnl'].sum() if len(result.trades) else 0.0,
        'avg_contracts': result.trades['contracts'].mean() if len(result.trades) else float('nan'),
        'pnl_per_contract': t['mean'],
        't_stat': t['t_stat'],
        'sharpe': m['Sharpe'],
        'vol': m['Volatility'],
        'max_dd_pct': m['Max Drawdown %'],
        'max_dd_usd': m['Max Drawdown $'],
        'cagr': m['CAGR'],
    }


def build_state(step_index: int, risk_pct: float):
    """Config/flags/padding after applying the first ``step_index`` steps."""
    cfg_kwargs = dict(LEGACY_CONFIG_KWARGS)
    flags = dataclasses.asdict(LEGACY_FLAGS)
    pad = True
    for name, kind in STEPS[:step_index]:
        if kind == 'flag':
            flags[name] = not LEGACY_FLAGS.__dict__[name]
        elif name.startswith('cost model'):
            cfg_kwargs.update(commission_per_side=2.50, slippage_sides=2)
        elif name.startswith('entry_mode'):
            cfg_kwargs.update(entry_mode='stop')
        elif name.startswith('drop calendar'):
            pad = False
    return replace(Config(risk_pct=risk_pct), **cfg_kwargs), SimFlags(**flags), pad


def published_endpoint_checks(first: dict, last: dict) -> list[tuple[str, float, float, bool]]:
    """Compare the 1% legacy/corrected endpoints with the published values."""
    expected = [
        ('legacy trades', first['trades'], 1728),
        ('legacy net PnL', round(first['net_pnl'], 0), 3287392.0),
        ('legacy Sharpe', round(first['sharpe'], 2), 0.95),
        ('corrected trades', last['trades'], 1725),
        ('corrected net PnL', round(last['net_pnl'], 0), 2800810.0),
        ('corrected Sharpe', round(last['sharpe'], 2), 1.02),
    ]
    return [
        (name, got, want, abs(got - want) < max(1.0, abs(want) * 1e-6))
        for name, got, want in expected
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--nq', type=Path, help='1-minute NQ csv (omit for synthetic bars)')
    ap.add_argument('--qqq', type=Path, help='daily QQQ csv (optional)')
    ap.add_argument('--risk', type=float, default=0.01)
    args = ap.parse_args()

    if args.nq:
        print(f'Loading {args.nq} ...')
        df_5min = resample_5min(load_nq_1min(args.nq))
        source = str(args.nq)
    else:
        from tests.synthetic import make_globex_feed
        print('No --nq given: using deterministic SYNTHETIC bars.')
        print('Synthetic results show the DIRECTION and rough scale of each effect.')
        print('They are NOT the published numbers and must not be quoted as such.\n')
        df_5min = make_globex_feed(n_days=520, seed=7)
        source = 'synthetic (520 days, seed=7)'

    print(f'Source: {source} | {len(df_5min):,} five-minute bars\n')

    rows = []
    prev = None
    for i, label in enumerate(['LEGACY (all original conventions)'] + [s[0] for s in STEPS]):
        cfg, flags, pad = build_state(i, args.risk)
        stats = summarise(run_backtest(df_5min, cfg, flags), pad)
        stats['step'] = label
        stats['d_sharpe'] = stats['sharpe'] - prev['sharpe'] if prev else float('nan')
        stats['d_pnl'] = stats['net_pnl'] - prev['net_pnl'] if prev else float('nan')
        stats['d_trades'] = stats['trades'] - prev['trades'] if prev else 0
        rows.append(stats)
        prev = stats

    out = pd.DataFrame(rows).set_index('step')
    pd.set_option('display.width', 200, 'display.max_columns', 50)

    print('=== Cumulative: each row adds one correction to the row above ===')
    print(out[['trades', 'd_trades', 'net_pnl', 'd_pnl', 'pnl_per_contract',
               't_stat', 'sharpe', 'd_sharpe', 'vol', 'max_dd_pct', 'cagr']]
          .to_string(float_format=lambda x: f'{x:,.4f}'))

    print('\n=== Endpoints ===')
    first, last = rows[0], rows[-1]
    for name, s in (('LEGACY   ', first), ('CORRECTED', last)):
        print(f'{name}: {s["trades"]:>5} trades | net PnL {s["net_pnl"]:>14,.0f} | '
              f'PnL/ct {s["pnl_per_contract"]:>8.2f} | t {s["t_stat"]:.2f} | '
              f'Sharpe {s["sharpe"]:.2f} | vol {s["vol"]:.2%} | '
              f'maxDD {s["max_dd_pct"]:.2%} | CAGR {s["cagr"]:.2%}')

    reproduction_failed = False
    if args.nq and abs(args.risk - 0.01) < 1e-12:
        print('\n=== Reproduction check against the published numbers ===')
        mismatches = []
        for name, got, want, matches in published_endpoint_checks(first, last):
            ok = 'MATCH  ' if matches else 'MISMATCH'
            print(f'  [{ok}] {name:<20} got {got:>14,} | published {want:>14,}')
            if not matches:
                mismatches.append(name)
        if mismatches:
            print(f'\nERROR: {len(mismatches)} published endpoint(s) did not reproduce: '
                  + ', '.join(mismatches), file=sys.stderr)
            reproduction_failed = True
    elif args.nq:
        print(f'\n=== Reproduction check skipped: published endpoints use '
              f'--risk 0.01, got {args.risk:g} ===')

    if args.qqq:
        from nq_breakout import align_benchmark
        qqq = load_qqq(args.qqq)
        cfg, flags, _ = build_state(len(STEPS), args.risk)
        res = run_backtest(df_5min, cfg, flags)
        bench = align_benchmark(qqq, res.equity.index, cfg.initial_capital)
        padded = compute_metrics(pad_to_calendar_days(bench))
        clean = compute_metrics(bench)
        print('\n=== Benchmark control (QQQ unchanged; only the date grid differs) ===')
        print(f'  calendar-padded : Sharpe {padded["Sharpe"]:.4f} | vol {padded["Volatility"]:.4%}')
        print(f'  trading-day only: Sharpe {clean["Sharpe"]:.4f} | vol {clean["Volatility"]:.4%}')
        if padded['Sharpe']:
            print(f'  ratio {clean["Sharpe"] / padded["Sharpe"]:.4f} '
                  f'(sqrt(365/252) = 1.2036 if padding is the whole story)')
    return 1 if reproduction_failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
