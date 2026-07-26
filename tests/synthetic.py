"""Synthetic bar generation for tests and for bias attribution.

The generator produces a near-continuous Globex-shaped 5-minute feed so
that engine conventions which only bite overnight (stop monitoring, CME
trading-day rollover) are actually exercised. It is deterministic given a
seed: no test depends on market data.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

__all__ = ['make_session_bars', 'make_globex_feed', 'bars_from_spec']


def bars_from_spec(spec: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Build an exact OHLC frame from ``(timestamp, o, h, l, c)`` tuples."""
    rows = [{'date': pd.Timestamp(ts), 'open': o, 'high': h, 'low': lo,
             'close': c, 'totalvolume': 100.0} for ts, o, h, lo, c in spec]
    return pd.DataFrame(rows)


def make_session_bars(day: dt.date, closes: np.ndarray, start: dt.time = dt.time(8, 30),
                      spread: float = 2.0) -> pd.DataFrame:
    """Wrap a close path into OHLC bars every 5 minutes from ``start``."""
    base = pd.Timestamp(day) + pd.Timedelta(hours=start.hour, minutes=start.minute)
    stamps = [base + pd.Timedelta(minutes=5 * i) for i in range(len(closes))]
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    return pd.DataFrame({'date': stamps, 'open': opens, 'high': highs,
                         'low': lows, 'close': closes, 'totalvolume': 100.0})


def make_globex_feed(n_days: int = 260, seed: int = 7, start_price: float = 15_000.0,
                     daily_vol: float = 0.012, drift: float = 0.0004,
                     start_date: dt.date = dt.date(2020, 1, 1)) -> pd.DataFrame:
    """A deterministic ~23h/day Globex-shaped 5-minute feed.

    Each weekday runs 17:00 (previous evening) through 16:00, with the
    16:00-17:00 maintenance halt absent from the data, matching the real
    file's shape. Volume is concentrated at the 08:30 cash open so the
    timezone diagnostic has a signature to find.
    """
    rng = np.random.default_rng(seed)
    bars_per_day = 276          # 23h of 5-minute bars
    per_bar_vol = daily_vol / np.sqrt(bars_per_day)
    per_bar_drift = drift / bars_per_day

    frames = []
    price = start_price
    day = start_date
    made = 0
    while made < n_days:
        if day.weekday() >= 5:
            day += dt.timedelta(days=1)
            continue
        # Session runs from 17:00 the previous evening to 16:00 today.
        session_open = pd.Timestamp(day) - pd.Timedelta(hours=7)
        stamps, opens, highs, lows, closes, vols = [], [], [], [], [], []
        for i in range(bars_per_day):
            ts = session_open + pd.Timedelta(minutes=5 * i)
            if ts.time() >= dt.time(16, 0) and ts.date() == day:
                break
            shock = rng.normal(per_bar_drift, per_bar_vol)
            o = price
            c = price * (1 + shock)
            wick = abs(rng.normal(0, per_bar_vol)) * price * 0.5
            stamps.append(ts)
            opens.append(o)
            highs.append(max(o, c) + wick)
            lows.append(min(o, c) - wick)
            closes.append(c)
            # Cash open (08:30 CT) carries the volume signature.
            vols.append(5_000.0 if dt.time(8, 30) <= ts.time() < dt.time(9, 0) else 400.0)
            price = c
        frames.append(pd.DataFrame({'date': stamps, 'open': opens, 'high': highs,
                                    'low': lows, 'close': closes, 'totalvolume': vols}))
        made += 1
        day += dt.timedelta(days=1)

    return pd.concat(frames, ignore_index=True).sort_values('date').reset_index(drop=True)
