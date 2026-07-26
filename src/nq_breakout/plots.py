"""Matplotlib figures for a backtest result.

Imported lazily by the notebook; matplotlib is an optional dependency of
the package (``pip install nq-breakout[notebook]``).
"""

from __future__ import annotations

import pandas as pd

from .backtest import BacktestResult

__all__ = ['plot_backtest', 'plot_vs_benchmark']


def plot_backtest(result: BacktestResult, title_prefix: str = '') -> None:
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    trades = result.trades.sort_values('exit_time').reset_index(drop=True)
    if trades.empty:
        print('No trades to plot.')
        return
    prefix = f'{title_prefix} — ' if title_prefix else ''

    plt.figure(figsize=(10, 4))
    for window, color in [(20, 'orange'), (50, 'teal'), (100, 'purple')]:
        rolling = trades['pnl_per_contract'].rolling(window=window, min_periods=1).mean()
        plt.plot(trades['exit_time'], rolling, label=f'Rolling {window}-trade avg', color=color)
    plt.xlabel('Exit Time'); plt.ylabel('PnL per contract ($)')
    plt.title(f'{prefix}Rolling PnL per Contract'); plt.legend(loc='upper left'); plt.grid()
    plt.show()

    plt.figure(figsize=(10, 4))
    entries = trades.sort_values('entry_time')
    ax = plt.gca()
    ax.step(entries['entry_time'], entries['contracts'], where='post', color='brown', alpha=0.8)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.xlabel('Entry Time'); plt.ylabel('Contracts')
    plt.title(f'{prefix}Contracts per Trade'); plt.grid()
    plt.show()

    plt.figure(figsize=(10, 4))
    risk = entries[entries['account_value_entry'] > 0]
    plt.plot(risk['entry_time'], risk['risk_pct'], color='darkgreen')
    plt.axhline(result.config.risk_pct * 100, color='gray', linestyle='--', linewidth=1,
                label='Configured Risk %')
    plt.xlabel('Entry Time'); plt.ylabel('Risk per Trade (% of equity)')
    plt.title(f'{prefix}Risk per Trade'); plt.legend(); plt.grid()
    plt.show()

    plt.figure(figsize=(10, 5))
    plt.plot(result.equity.index, result.equity / 1e6, color='purple')
    plt.xlabel('Date'); plt.ylabel('Account Value ($M)')
    plt.title(f'{prefix}Daily Mark-to-Market Equity'); plt.grid()
    plt.show()


def plot_vs_benchmark(result: BacktestResult, benchmark_equity: pd.Series,
                      benchmark_name: str = 'QQQ Buy & Hold') -> None:
    import matplotlib.pyplot as plt

    initial = result.config.initial_capital
    plt.figure(figsize=(10, 5))
    plt.plot(result.equity.index, (result.equity - initial) / 1e6, label='Strategy PnL (MTM)', color='blue')
    plt.plot(benchmark_equity.index, (benchmark_equity - initial) / 1e6,
             label=f'{benchmark_name} PnL', color='orange', linestyle='dashed')
    plt.xlabel('Date'); plt.ylabel('PnL ($M)')
    plt.title(f'Strategy vs {benchmark_name}'); plt.legend(); plt.grid()
    plt.show()
