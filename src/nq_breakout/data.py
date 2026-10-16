"""Loading and resampling of the raw market data files.

See ``data/README.md`` for the expected CSV schema and the vendor the
files came from. Nothing in this module is strategy-specific: it turns
files on disk into tidy OHLCV frames and does nothing else.
"""

from __future__ import annotations

import pandas as pd

__all__ = ['load_nq_1min', 'resample_5min', 'load_qqq']


def load_nq_1min(path, stamped_at: str = 'start') -> pd.DataFrame:
    """Load the raw 1-minute NQ CSV.

    Expected columns: ``Date`` (``%d/%m/%Y``), ``Time`` (``%H:%M:%S``),
    ``Open``, ``High``, ``Low``, ``Close``, ``TotalVolume``.

    Timestamps carry no timezone. The feed used for the published results
    is stamped in **US/Central** time, not Eastern — see
    ``diagnostics.run_data_diagnostics``, which detects this from the
    cash-open volume signature.

    ``stamped_at`` says which edge of the minute the file's timestamps
    mark. Everything downstream assumes ``'start'`` (bar ``08:30`` covers
    08:30–08:31). MultiCharts exports stamp bars at their *end* (the bar
    covering 08:29–08:30 is written as ``08:30``); pass ``'end'`` for such
    a file and every timestamp is moved back one minute so it labels the
    bar's start. ``run_data_diagnostics`` reports which convention a file
    appears to use. The published results were produced with ``'start'``.

    ``pandas.read_csv`` handles ``.csv`` and ``.csv.gz`` transparently.
    """
    if stamped_at not in ('start', 'end'):
        raise ValueError(f"stamped_at must be 'start' or 'end', got {stamped_at!r}")
    raw = pd.read_csv(path)
    missing = {'Date', 'Time', 'Open', 'High', 'Low', 'Close', 'TotalVolume'} - set(raw.columns)
    if missing:
        raise ValueError(
            f'NQ 1-minute file is missing required column(s): {sorted(missing)}. '
            'See data/README.md for the expected schema.'
        )
    raw['date'] = pd.to_datetime(raw['Date'] + ' ' + raw['Time'], format='%d/%m/%Y %H:%M:%S')
    if stamped_at == 'end':
        raw['date'] -= pd.Timedelta(minutes=1)
    raw = raw.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low',
                              'Close': 'close', 'TotalVolume': 'totalvolume'})
    return raw[['date', 'open', 'high', 'low', 'close', 'totalvolume']].sort_values('date')


def resample_5min(df_1min: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1-minute bars to 5-minute bars labelled by their LEFT edge.

    Bar ``10:00`` therefore covers ``10:00:00``–``10:04:59``. Buckets with
    no underlying ticks (the daily maintenance halt, weekends) are dropped
    rather than forward-filled, so the engine never sees a synthetic bar.
    """
    return (df_1min.set_index('date')
            .resample('5min')
            .agg({'open': 'first', 'high': 'max', 'low': 'min',
                  'close': 'last', 'totalvolume': 'sum'})
            .dropna(subset=['open', 'high', 'low', 'close'])
            .reset_index())


def load_qqq(path) -> pd.DataFrame:
    """Load the daily QQQ benchmark closes.

    Accepts either lowercase ``date``/``close`` columns or the usual
    vendor capitalisation, including ``Adj Close``.
    """
    qqq = pd.read_csv(path)
    if 'date' not in qqq.columns or 'close' not in qqq.columns:
        rename_map = {}
        for column in qqq.columns:
            lower = column.lower().strip()
            if lower == 'date':
                rename_map[column] = 'date'
            elif lower in {'close', 'adj close', 'adj_close'}:
                rename_map[column] = 'close'
        qqq = qqq.rename(columns=rename_map)
        if 'date' not in qqq.columns or 'close' not in qqq.columns:
            raise ValueError('QQQ_daily.csv must contain "date" and "close" columns.')
    qqq['date'] = pd.to_datetime(qqq['date'], errors='coerce')
    qqq['close'] = pd.to_numeric(qqq['close'], errors='coerce')
    qqq = qqq.dropna(subset=['date', 'close']).sort_values('date').reset_index(drop=True)
    if qqq.empty:
        raise ValueError('QQQ_daily.csv has no valid rows after cleaning.')
    return qqq
