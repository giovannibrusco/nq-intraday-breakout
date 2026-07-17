"""NQ intraday breakout backtest engine.

Extracted from the original notebook so the same engine can be run with
different risk budgets / entry conventions without duplicating code.

Simulation conventions (and their limits):
- Bars are 5-minute OHLC labelled by the *left* edge (bar "10:00" covers
  10:00:00-10:04:59). The breakout window is every bar labelled strictly
  before ``window_end``; the first tradable bar is the one labelled
  ``trade_start``.
- ``entry_mode='stop'`` models a stop order resting at the window extreme:
  the fill is at the breakout level, or at the bar open when the bar opens
  through the level (gap). ``entry_mode='close'`` reproduces the legacy
  notebook fill at the close of the breakout bar.
- Stops and targets are gap-aware: a bar opening beyond the level fills at
  the open, not at the level. When a bar touches both stop and target the
  stop is assumed to hit first (conservative).
- Exits are evaluated from the bar *after* the entry bar: the intra-bar
  sequence of the entry bar is unknowable from OHLC.
- Equity is marked to market at the close of the last bar of every session,
  including open positions. Position sizing uses this MTM equity.
- ``margin_per_contract`` is static (today's IB margin applied to the whole
  history); historical margins were materially lower, so early-sample
  sizing under the margin cap is conservative rather than accurate.
- The engine simulates every bar present in the input, including overnight
  ones: if the feed only covers the day session (as in the unit-test
  fixtures), overnight moves are only seen as an opening gap on the next
  session's first bar; if the feed is a near-continuous Globex file (as
  the real NQ data is), stops and targets are checked bar-by-bar through
  the night, catching the exact crossing instead of approximating it with
  the next session's open.
- A "trading day" is not a calendar date: bars at/after the daily Globex
  halt (``regular_end``, the exchange's native maintenance break) belong
  to the *next* calendar date's trading day, matching how CME sessions are
  conventionally labelled. Window formation, entries, ``max_long_hold_days``
  and the daily mark-to-market are all keyed off this trading-day mapping.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Config:
    point_value: float = 20.0          # $ per NQ index point
    tick_size: float = 0.25            # index points per tick
    tick_value: float = 5.0            # $ per tick (tick_size * point_value)
    stop_dollars: float = 2_000.0      # per contract
    target_dollars: float = 4_000.0    # per contract
    initial_capital: float = 1_000_000.0
    margin_per_contract: float = 23_282.57  # static IB NQ margin, see module docstring
    margin_buffer: float = 0.15
    risk_pct: float = 0.01
    # All-in cost per side per contract (broker commission + exchange + NFA
    # fees). IB's $0.85 is the broker commission alone; all-in is ~$2.50.
    commission_per_side: float = 2.50
    slippage_ticks_per_side: float = 1.5
    window_start: dt.time = dt.time(8, 30)
    window_end: dt.time = dt.time(10, 0)   # exclusive: window bars are labelled < window_end
    trade_start: dt.time = dt.time(10, 0)
    trade_end: dt.time = dt.time(14, 30)
    regular_start: dt.time = dt.time(9, 30)
    regular_end: dt.time = dt.time(16, 0)
    max_trades_per_day: int = 2
    max_long_hold_days: int = 5
    entry_mode: str = 'stop'           # 'stop' | 'close'

    @property
    def stop_points(self) -> float:
        return self.stop_dollars / self.point_value

    @property
    def target_points(self) -> float:
        return self.target_dollars / self.point_value

    @property
    def round_turn_cost(self) -> float:
        """Total $ cost per contract per round turn (fees + slippage, both sides)."""
        return 2 * self.commission_per_side + 2 * self.slippage_ticks_per_side * self.tick_value

    def __post_init__(self):
        if self.entry_mode not in ('stop', 'close'):
            raise ValueError(f"entry_mode must be 'stop' or 'close', got {self.entry_mode!r}")


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series          # daily MTM equity indexed by trading day
    config: Config


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_nq_1min(path) -> pd.DataFrame:
    """Load the raw 1-minute CSV (Date/Time/Open/High/Low/Close/TotalVolume)."""
    raw = pd.read_csv(path)
    raw['date'] = pd.to_datetime(raw['Date'] + ' ' + raw['Time'], format='%d/%m/%Y %H:%M:%S')
    raw = raw.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low',
                              'Close': 'close', 'TotalVolume': 'totalvolume'})
    return raw[['date', 'open', 'high', 'low', 'close', 'totalvolume']].sort_values('date')


def resample_5min(df_1min: pd.DataFrame) -> pd.DataFrame:
    return (df_1min.set_index('date')
            .resample('5min')
            .agg({'open': 'first', 'high': 'max', 'low': 'min',
                  'close': 'last', 'totalvolume': 'sum'})
            .dropna(subset=['open', 'high', 'low', 'close'])
            .reset_index())


def load_qqq(path) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# Data diagnostics — run these before trusting any backtest number
# ---------------------------------------------------------------------------

def run_data_diagnostics(df_1min: pd.DataFrame, config: Config = Config()) -> None:
    """Print sanity checks on the raw feed: timezone, roll gaps, overnight gaps.

    The session logic assumes timestamps are US/Eastern. The continuous
    contract must be back-adjusted, otherwise multi-day trades book roll
    basis jumps as PnL. Neither can be verified from the repo, only from
    the data — hence this report.
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
    if likely_open == dt.time(9, 30):
        print('Consistent with US/Eastern timestamps (cash open 09:30 ET). OK.')
    elif likely_open == dt.time(8, 30):
        print('WARNING: volume spike at 08:30 suggests Central Time timestamps '
              '(cash open 08:30 CT). All session times in Config would be shifted '
              'by one hour — fix the data before trusting any result.')
    else:
        print(f'WARNING: unexpected open at {likely_open}; inspect the feed manually.')

    # --- roll / splice check: close->open jumps far beyond the typical gap.
    daily = df.groupby('day').agg(first_open=('open', 'first'), last_close=('close', 'last'))
    gaps = (daily['first_open'] - daily['last_close'].shift(1)).dropna()
    typical = gaps.abs().median()
    threshold = max(10 * typical, 20.0)
    outliers = gaps[gaps.abs() > threshold]
    roll_months = {3, 6, 9, 12}
    in_roll_month = sum(1 for d in outliers.index if d.month in roll_months)
    print('\n== Roll / splice check ==')
    print(f'Median |overnight gap|: {typical:.2f} pts. '
          f'Gaps beyond {threshold:.0f} pts: {len(outliers)} '
          f'({in_roll_month} in quarterly roll months).')
    if len(outliers) > 0 and in_roll_month / len(outliers) > 0.6:
        print('WARNING: large gaps cluster in Mar/Jun/Sep/Dec — the continuous '
              'series looks spliced WITHOUT back-adjustment. Multi-day trades '
              'would book roll basis as PnL.')
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


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def position_size(equity: float, stop_pts: float, config: Config) -> int:
    """Contracts as min(risk budget, margin cap); 0 when neither allows one."""
    if equity <= 0:
        return 0
    stop_pts = max(stop_pts, 1e-9)
    n_risk = (config.risk_pct * equity) / (stop_pts * config.point_value)
    n_margin = equity / (config.margin_per_contract * (1 + config.margin_buffer))
    return max(0, int(min(n_risk, n_margin)))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _trading_day(ts: pd.Timestamp, config: Config) -> dt.date:
    """Map a bar's timestamp to the trading day whose regular session it
    feeds into. Bars strictly after the daily Globex halt (``regular_end``)
    lead into the *next* calendar date's session — e.g. a bar timestamped
    Monday 17:00 belongs to Tuesday's trading day. The bar labelled exactly
    ``regular_end`` (the last one before the halt, e.g. Monday 16:00) is
    still part of Monday: the halt empties out the following 5-minute
    buckets, so the next bar with data is the first one strictly after it."""
    d = ts.date()
    if ts.time() > config.regular_end:
        return d + dt.timedelta(days=1)
    return d


def _prepare(df_5min: pd.DataFrame, config: Config) -> pd.DataFrame:
    df = df_5min.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['time'] = df['date'].dt.time
    df['trading_day'] = df['date'].map(lambda ts: _trading_day(ts, config))
    return df


def run_backtest(df_5min: pd.DataFrame, config: Config) -> BacktestResult:
    df = _prepare(df_5min, config)
    if df.empty:
        raise ValueError('No bars in the input data.')

    # A "trading day" only counts (for days_in_trade / max_long_hold_days)
    # if it appears anywhere in the feed; a trading day whose calendar date
    # is a holiday with overnight-only Globex activity and no actual
    # window/trade-hours bars would still occupy a slot here, a minor
    # conservative approximation (see module docstring).
    trading_days = sorted(df['trading_day'].unique())
    day_index = {day: i for i, day in enumerate(trading_days)}

    session_mask = (df['time'] >= config.window_start) & (df['time'] <= config.trade_end)
    session_close_bar = df[session_mask].groupby('trading_day')['date'].max().to_dict()

    trades: list[dict] = []
    day_marks: list[tuple] = []      # (trading_day, mtm_equity) recorded at each session's last bar
    account_value = config.initial_capital
    open_trade: dict | None = None

    current_trading_day = None
    daily_trade_count = 0
    window_high = None
    window_low = None
    window_ready = False
    prev_row_timestamp = None
    prev_row_close = None

    def unrealized(mark_price: float) -> float:
        if open_trade is None:
            return 0.0
        sign = 1.0 if open_trade['direction'] == 'long' else -1.0
        return sign * (mark_price - open_trade['entry_price']) * config.point_value * open_trade['contracts']

    def close_trade(exit_time, exit_price, exit_reason, exit_day):
        nonlocal open_trade, account_value
        contracts = open_trade['contracts']
        sign = 1.0 if open_trade['direction'] == 'long' else -1.0
        pnl_per_contract = sign * (exit_price - open_trade['entry_price']) * config.point_value
        pnl_per_contract -= config.round_turn_cost
        pnl = pnl_per_contract * contracts
        account_value += pnl
        trades.append({
            'direction': open_trade['direction'],
            'entry_time': open_trade['entry_time'],
            'exit_time': exit_time,
            'entry_price': open_trade['entry_price'],
            'exit_price': exit_price,
            'contracts': contracts,
            'stop_points': open_trade['stop_pts'],
            'pnl_per_contract': pnl_per_contract,
            'pnl': pnl,
            'fees': 2 * config.commission_per_side * contracts,
            'exit_reason': exit_reason,
            'days_in_trade': day_index[exit_day] - day_index[open_trade['entry_day']] + 1,
            'account_value': account_value,
            'risk_dollars': open_trade['risk_dollars'],
            'risk_pct': open_trade['risk_pct'],
            'account_value_entry': open_trade['account_value_entry'],
        })
        open_trade = None

    for row in df.itertuples(index=False):
        row_timestamp = row.date
        row_trading_day = row.trading_day
        row_time = row.time
        is_last_bar = session_close_bar.get(row_trading_day) == row_timestamp

        if current_trading_day is None:
            current_trading_day = row_trading_day
        if row_trading_day != current_trading_day:
            # Safety net for trading days that never reach a session close
            # bar (e.g. a data gap): shorts must not survive into the next
            # trading day.
            if open_trade is not None and open_trade['direction'] == 'short':
                close_trade(prev_row_timestamp, prev_row_close, 'eod_short', current_trading_day)
            current_trading_day = row_trading_day
            daily_trade_count = 0
            window_high = None
            window_low = None
            window_ready = False

        in_window = config.window_start <= row_time < config.window_end
        if in_window:
            window_high = row.high if window_high is None else max(window_high, row.high)
            window_low = row.low if window_low is None else min(window_low, row.low)
        if not window_ready and row_time >= config.window_end and window_high is not None:
            window_ready = True

        # ---- exits (evaluated from the bar after the entry bar, on EVERY
        # bar in the feed — including overnight ones when present, so a
        # stop/target is caught at the exact crossing rather than only at
        # the next session's opening gap) ----
        exited_this_bar = False
        if open_trade is not None and row_timestamp > open_trade['entry_time']:
            exit_price = None
            exit_reason = None
            days_in_trade = day_index[row_trading_day] - day_index[open_trade['entry_day']] + 1
            stop = open_trade['stop']
            target = open_trade['target']
            if open_trade['direction'] == 'long':
                if row.open <= stop:
                    exit_price, exit_reason = row.open, 'stop'
                elif row.low <= stop:
                    exit_price, exit_reason = stop, 'stop'
                elif row.open >= target:
                    exit_price, exit_reason = row.open, 'target'
                elif row.high >= target:
                    exit_price, exit_reason = target, 'target'
                elif days_in_trade >= config.max_long_hold_days and is_last_bar:
                    exit_price, exit_reason = row.close, 'max_hold'
            else:
                if row.open >= stop:
                    exit_price, exit_reason = row.open, 'stop'
                elif row.high >= stop:
                    exit_price, exit_reason = stop, 'stop'
                elif row.open <= target:
                    exit_price, exit_reason = row.open, 'target'
                elif row.low <= target:
                    exit_price, exit_reason = target, 'target'
                elif is_last_bar:
                    exit_price, exit_reason = row.close, 'eod_short'
            if exit_price is not None:
                close_trade(row_timestamp, exit_price, exit_reason, row_trading_day)
                exited_this_bar = True

        # ---- entries (never on the same bar as an exit) ----
        if (open_trade is None and not exited_this_bar and window_ready
                and daily_trade_count < config.max_trades_per_day
                and config.trade_start <= row_time <= config.trade_end
                and not is_last_bar):
            direction = None
            entry_price = None
            if row.high > window_high:
                direction = 'long'
                entry_price = max(window_high, row.open) if config.entry_mode == 'stop' else row.close
            elif row.low < window_low:
                direction = 'short'
                entry_price = min(window_low, row.open) if config.entry_mode == 'stop' else row.close
            if direction is not None:
                stop_pts = config.stop_points
                contracts = position_size(account_value, stop_pts, config)
                if contracts > 0:
                    sign = 1.0 if direction == 'long' else -1.0
                    risk_dollars = stop_pts * config.point_value * contracts
                    open_trade = {
                        'direction': direction,
                        'entry_time': row_timestamp,
                        'entry_price': entry_price,
                        'stop': entry_price - sign * stop_pts,
                        'target': entry_price + sign * config.target_points,
                        'entry_day': row_trading_day,
                        'stop_pts': stop_pts,
                        'contracts': contracts,
                        'risk_dollars': risk_dollars,
                        'account_value_entry': account_value,
                        'risk_pct': risk_dollars / account_value * 100,
                    }
                    daily_trade_count += 1

        if is_last_bar:
            day_marks.append((row_trading_day, account_value + unrealized(row.close)))

        prev_row_timestamp = row_timestamp
        prev_row_close = row.close

    if open_trade is not None:
        close_trade(prev_row_timestamp, prev_row_close, 'force_close', current_trading_day)
    # A force_close on the same trading day as the last recorded mark means
    # that mark was still an unrealized (pre-close) snapshot — replace it
    # with the now-realized, cost-inclusive value instead of stacking a
    # second entry for the same day.
    if day_marks and day_marks[-1][0] == current_trading_day:
        day_marks[-1] = (current_trading_day, account_value)
    else:
        day_marks.append((current_trading_day, account_value))

    equity = pd.Series({pd.Timestamp(day): value for day, value in day_marks}, name='equity')
    equity.index.name = 'date'
    trades_df = pd.DataFrame(trades)
    return BacktestResult(trades=trades_df, equity=equity, config=config)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(equity: pd.Series, freq: int = TRADING_DAYS_PER_YEAR) -> dict:
    """Metrics on a trading-day equity series (no calendar-day padding)."""
    returns = equity.pct_change().dropna()
    if returns.empty:
        return {k: np.nan for k in ('Sharpe', 'Volatility', 'Max Drawdown %',
                                    'Max Drawdown $', 'CAGR')}
    std_ret = returns.std(ddof=1)
    sharpe = returns.mean() / std_ret * math.sqrt(freq) if std_ret > 0 else np.nan
    running_max = equity.cummax()
    drawdown_abs = equity - running_max
    total_years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / total_years) - 1 if total_years > 0 else np.nan
    return {
        'Sharpe': sharpe,
        'Volatility': std_ret * math.sqrt(freq),
        'Max Drawdown %': (drawdown_abs / running_max).min(),
        'Max Drawdown $': drawdown_abs.min(),
        'CAGR': cagr,
    }


def trade_pnl_tstat(trades: pd.DataFrame) -> dict:
    """t-stat of the mean per-contract trade PnL (H0: zero edge net of costs)."""
    pnl = trades['pnl_per_contract'].dropna()
    n = len(pnl)
    if n < 2:
        return {'mean': np.nan, 'std': np.nan, 'n': n, 't_stat': np.nan}
    mean, std = pnl.mean(), pnl.std(ddof=1)
    return {'mean': mean, 'std': std, 'n': n,
            't_stat': mean / (std / math.sqrt(n)) if std > 0 else np.nan}


def align_benchmark(qqq: pd.DataFrame, equity_index: pd.DatetimeIndex,
                    initial_capital: float) -> pd.Series:
    """Buy&hold benchmark equity marked on the strategy's trading days."""
    close = qqq.set_index('date')['close'].reindex(equity_index, method='ffill').bfill()
    if close.isna().any():
        raise ValueError('Benchmark data does not cover the strategy period.')
    return initial_capital / close.iloc[0] * close


def metrics_table(named_equities: dict[str, pd.Series]) -> pd.DataFrame:
    return pd.DataFrame({name: compute_metrics(eq) for name, eq in named_equities.items()}).T


def format_metrics(df: pd.DataFrame) -> pd.DataFrame:
    formatted = df.copy()
    fmt = {
        'Sharpe': lambda x: f'{x:.2f}',
        'Volatility': lambda x: f'{x:.2%}',
        'Max Drawdown %': lambda x: f'{abs(x):.2%}',
        'Max Drawdown $': lambda x: f'{x:,.0f}',
        'CAGR': lambda x: f'{x:.2%}',
    }
    for col, f in fmt.items():
        if col in formatted.columns:
            formatted[col] = formatted[col].map(lambda x: f(x) if pd.notnull(x) else '—')
    return formatted


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

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


__all__ = [
    'Config', 'BacktestResult', 'load_nq_1min', 'load_qqq', 'resample_5min',
    'run_data_diagnostics', 'position_size', 'run_backtest', 'compute_metrics',
    'trade_pnl_tstat', 'align_benchmark', 'metrics_table', 'format_metrics',
    'plot_backtest', 'plot_vs_benchmark', 'replace',
]
