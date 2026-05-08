"""Run baseline + long-only backtests, extract daily PnL series for MC."""
import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.strategy.config import load_strategy_config, load_instrument_spec
from nqbreakout.strategy.core import run_backtest


def daily_pnl_from_trades(trades: pd.DataFrame) -> pd.Series:
    """Aggregate trade pnl by exit date. Returns Series indexed by date."""
    if trades.empty:
        return pd.Series(dtype=float, name='daily_pnl')
    s = trades.copy()
    s['exit_date'] = pd.to_datetime(s['exit_time']).dt.normalize()
    return s.groupby('exit_date')['pnl'].sum().rename('daily_pnl')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='data/nq_1min.parquet')
    parser.add_argument('--config', default='configs/strategy_default.yaml')
    parser.add_argument('--out-dir', default='data/daily_pnl')
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df_5min = resample_to_5min(load_nq_1min(args.data))
    instr = load_instrument_spec('NQ')

    # Long+short baseline
    config_baseline = load_strategy_config(args.config)
    res_baseline = run_backtest(df_5min, config_baseline, instr)
    pnl_baseline = daily_pnl_from_trades(res_baseline.trades)
    pnl_baseline.to_frame().to_parquet(out_dir / 'baseline.parquet')
    print(f'baseline: {len(res_baseline.trades)} trades, {len(pnl_baseline)} trading days, total ${pnl_baseline.sum():,.0f}')

    # Long-only variant
    from dataclasses import replace
    config_long = replace(config_baseline, long_only=True)
    res_long = run_backtest(df_5min, config_long, instr)
    pnl_long = daily_pnl_from_trades(res_long.trades)
    pnl_long.to_frame().to_parquet(out_dir / 'long_only.parquet')
    print(f'long_only: {len(res_long.trades)} trades, {len(pnl_long)} trading days, total ${pnl_long.sum():,.0f}')


if __name__ == '__main__':
    main()
