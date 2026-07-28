"""Tests for loading and resampling. No real data file is touched."""

from __future__ import annotations

import pandas as pd
import pytest

from nq_breakout import load_nq_1min, load_qqq, resample_5min


@pytest.fixture
def nq_csv(tmp_path):
    """A 1-minute file in the vendor's schema: 12 minutes on one day."""
    stamps = pd.date_range('2023-01-02 09:00', periods=12, freq='1min')
    df = pd.DataFrame({
        'Date': stamps.strftime('%d/%m/%Y'),
        'Time': stamps.strftime('%H:%M:%S'),
        'Open': range(100, 112),
        'High': range(105, 117),
        'Low': range(95, 107),
        'Close': range(101, 113),
        'TotalVolume': [10] * 12,
    })
    path = tmp_path / 'nq.csv'
    df.to_csv(path, index=False)
    return path


def test_load_nq_parses_day_first_dates(nq_csv):
    df = load_nq_1min(nq_csv)
    assert list(df.columns) == ['date', 'open', 'high', 'low', 'close', 'totalvolume']
    assert df['date'].iloc[0] == pd.Timestamp('2023-01-02 09:00')
    assert df['date'].is_monotonic_increasing


def test_load_nq_rejects_missing_columns(tmp_path):
    path = tmp_path / 'bad.csv'
    pd.DataFrame({'Date': ['02/01/2023'], 'Close': [1.0]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match='missing required column'):
        load_nq_1min(path)


def test_load_nq_reads_gzip(nq_csv, tmp_path):
    gz = tmp_path / 'nq.csv.gz'
    pd.read_csv(nq_csv).to_csv(gz, index=False, compression='gzip')
    pd.testing.assert_frame_equal(load_nq_1min(gz), load_nq_1min(nq_csv))


def test_resample_aggregates_ohlcv_correctly(nq_csv):
    one_min = load_nq_1min(nq_csv)
    five = resample_5min(one_min)
    assert len(five) == 3                       # 12 minutes -> 3 buckets
    first = five.iloc[0]
    src = one_min.iloc[0:5]
    assert first['date'] == pd.Timestamp('2023-01-02 09:00')   # left-labelled
    assert first['open'] == src['open'].iloc[0]
    assert first['high'] == src['high'].max()
    assert first['low'] == src['low'].min()
    assert first['close'] == src['close'].iloc[-1]
    assert first['totalvolume'] == src['totalvolume'].sum()


def test_resample_drops_empty_buckets():
    """Gaps (the maintenance halt) must not become synthetic flat bars."""
    stamps = list(pd.date_range('2023-01-02 09:00', periods=5, freq='1min'))
    stamps += list(pd.date_range('2023-01-02 10:00', periods=5, freq='1min'))
    one_min = pd.DataFrame({'date': stamps, 'open': 1.0, 'high': 2.0,
                            'low': 0.5, 'close': 1.5, 'totalvolume': 1})
    five = resample_5min(one_min)
    assert len(five) == 2
    assert list(five['date']) == [pd.Timestamp('2023-01-02 09:00'),
                                  pd.Timestamp('2023-01-02 10:00')]


def test_load_qqq_normalises_column_names(tmp_path):
    path = tmp_path / 'qqq.csv'
    pd.DataFrame({'Date': ['2023-01-03', '2023-01-04'],
                  'Adj Close': [260.0, 262.5]}).to_csv(path, index=False)
    qqq = load_qqq(path)
    assert list(qqq.columns) == ['date', 'close']
    assert qqq['close'].tolist() == [260.0, 262.5]


def test_load_qqq_drops_unparseable_rows(tmp_path):
    path = tmp_path / 'qqq.csv'
    pd.DataFrame({'date': ['2023-01-03', 'not-a-date', '2023-01-05'],
                  'close': [260.0, 261.0, 'oops']}).to_csv(path, index=False)
    qqq = load_qqq(path)
    assert len(qqq) == 1
    assert qqq['date'].iloc[0] == pd.Timestamp('2023-01-03')


def test_load_qqq_rejects_missing_columns(tmp_path):
    path = tmp_path / 'qqq.csv'
    pd.DataFrame({'foo': [1], 'bar': [2]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match='must contain'):
        load_qqq(path)


def test_load_qqq_rejects_all_invalid(tmp_path):
    path = tmp_path / 'qqq.csv'
    pd.DataFrame({'date': ['x'], 'close': ['y']}).to_csv(path, index=False)
    with pytest.raises(ValueError, match='no valid rows'):
        load_qqq(path)
