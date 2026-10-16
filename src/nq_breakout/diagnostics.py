"""Sanity checks on the raw feed. Run these before trusting any number.

None of these can be verified from the repository alone -- only from the
data file the user supplies -- which is why they are a runtime report
rather than a test.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .backtest import Config

__all__ = ['run_data_diagnostics']


def run_data_diagnostics(df_1min: pd.DataFrame, config: Config = Config()) -> None:
    """Print sanity checks on the raw feed: timezone, bar stamps, roll and overnight gaps.

    The session times in :class:`Config` only mean what the README says
    they mean if the feed is stamped in US/Central. The continuous
    contract must also be back-adjusted, otherwise multi-day trades book
    roll basis jumps as PnL. Neither can be checked from the repository,
    only from the data file — hence this runtime report.
    """
    df = df_1min.copy()
    df['time'] = df['date'].dt.time
    df['day'] = df['date'].dt.date

    # --- timezone check: the cash-open volume spike should sit at 09:30 ET.
    df['bucket'] = df['date'].dt.floor('30min').dt.time
    vol_by_bucket = df.groupby('bucket')['totalvolume'].mean()
    jump = (vol_by_bucket / vol_by_bucket.shift(1)).dropna()
    likely_open = jump.idxmax()
    print('== Timezone check ==')
    print(f'Largest average-volume jump starts at {likely_open} '
          f'(x{jump.max():.1f} vs the previous half hour).')
    if likely_open == dt.time(8, 30):
        print('Consistent with US/Central timestamps (cash open 08:30 CT = 09:30 ET). '
              f'The Config window {config.window_start}-{config.window_end} is therefore '
              'the first 90 minutes of the cash session. OK.')
    elif likely_open == dt.time(9, 30):
        print('WARNING: the spike sits at 09:30, which means the feed is stamped in '
              'US/Eastern, not Central. The Config window would then start a full hour '
              'before the cash open and this would be a pre-market breakout, not an '
              'opening-range one. Shift the session times before trusting any result.')
    else:
        print(f'WARNING: unexpected open at {likely_open}; inspect the feed manually.')

    # --- bar timestamp convention: the session reopens at 17:00 CT after
    # the maintenance halt. A start-stamped file's first bar is 17:00; an
    # end-stamped one (MultiCharts exports) writes that same minute as 17:01.
    reopen = df[(df['time'] >= dt.time(17, 0)) & (df['time'] < dt.time(17, 5))]
    first_after_halt = reopen.groupby('day')['time'].min()
    print('\n== Bar timestamp check ==')
    if first_after_halt.empty:
        print('No bars between 17:00 and 17:05; cannot tell whether timestamps '
              'mark the start or the end of each bar.')
    else:
        modal = first_after_halt.mode().iloc[0]
        share = (first_after_halt == modal).mean()
        print(f'First bar after the maintenance halt is most often {modal} '
              f'({share:.0%} of {len(first_after_halt)} sessions).')
        if modal == dt.time(17, 0):
            print('Consistent with bars stamped at their START, which is what the '
                  'engine assumes. OK.')
        elif modal == dt.time(17, 1):
            print("WARNING: bars appear to be stamped at their END (MultiCharts "
                  "convention). Reload with load_nq_1min(path, stamped_at='end'), "
                  'otherwise every 5-minute bar is shifted by one minute and the '
                  'opening range includes the 08:29-08:30 minute.')
        else:
            print('WARNING: unexpected first bar after the halt; inspect the feed manually.')

    # --- roll / splice check: close->open jumps far beyond the typical gap.
    daily = df.groupby('day').agg(first_open=('open', 'first'), last_close=('close', 'last'))
    gaps = (daily['first_open'] - daily['last_close'].shift(1)).dropna()
    typical = gaps.abs().median()
    threshold = max(10 * typical, 20.0)
    outliers = gaps[gaps.abs() > threshold]
    roll_months = {3, 6, 9, 12}
    in_roll_month = sum(1 for d in outliers.index if d.month in roll_months)
    print('\n== Roll / splice check ==')
    # Roll months are 4 of 12, so ~33% of outliers landing in them is what
    # chance alone produces. Only a share well above that is evidence of an
    # un-adjusted splice, and a share near it is NOT evidence of adjustment.
    share = in_roll_month / len(outliers) if len(outliers) else float('nan')
    print(f'Median |overnight gap|: {typical:.2f} pts. '
          f'Gaps beyond {threshold:.0f} pts: {len(outliers)} '
          f'({in_roll_month} in quarterly roll months, {share:.0%}; '
          f'~33% expected by chance).')
    if len(outliers) > 0 and share > 0.6:
        print('WARNING: large gaps cluster in Mar/Jun/Sep/Dec — the continuous '
              'series looks spliced WITHOUT back-adjustment. Multi-day trades '
              'would book roll basis as PnL.')
    elif len(outliers) > 0:
        print('No roll-month clustering detected. Note this is only absence of '
              'evidence: it does not confirm the series IS back-adjusted, since '
              'a correctly adjusted series and a lightly spliced one look alike '
              'at this resolution. Confirm with the vendor.')
    if not outliers.empty:
        print(outliers.sort_values(key=abs, ascending=False).head(10).to_string())

    # --- overnight gap-through-stop exposure.
    stop_pts = config.stop_points
    through = gaps[gaps.abs() > stop_pts]
    print('\n== Overnight gap risk ==')
    print(f'Sessions opening more than {stop_pts:.0f} pts (the stop distance) away '
          f'from the prior close: {len(through)} of {len(gaps)} '
          f'({len(through) / max(len(gaps), 1):.1%}).')
    rows_per_day = len(df) / max(df['day'].nunique(), 1)
    if rows_per_day > 300:
        print(f'The feed averages {rows_per_day:.0f} bars/day — consistent with '
              'near-continuous Globex coverage. run_backtest walks every bar, '
              'including overnight ones, so stops/targets are caught at the exact '
              'crossing rather than approximated by these opening gaps.')
    else:
        print(f'The feed averages only {rows_per_day:.0f} bars/day — consistent with '
              'day-session-only coverage. Overnight moves are invisible to '
              'run_backtest except as the opening gap on the next session\'s first '
              'bar (the gap-aware fill logic handles that case, but a stop crossed '
              'and reversed overnight would be missed entirely).')
