"""Event-driven backtest engine for the opening-range breakout.

The engine walks 5-minute bars in order and maintains at most one open
position. Every simulation convention that is *debatable* is exposed as a
flag on :class:`SimFlags` rather than being hard-coded, because the
repository has two published result sets produced by two different sets of
those conventions (see ``docs/bias-audit.md``):

- ``SimFlags()``       — the corrected conventions (current default).
- ``LEGACY_FLAGS``     — the conventions of the original 2025 notebook,
  which produced the results published in the README on ``main``.

Running the same bars under both, and flipping one flag at a time, is what
``tools/bias_attribution.py`` does to attribute the difference between the
two result sets to individual causes.

Bar and session conventions
---------------------------
Bars are 5-minute OHLC labelled by their *left* edge (bar "10:00" covers
10:00:00-10:04:59). Timestamps carry no timezone; the feed behind the
published results is US/Central, so the ``08:30-10:00`` window is the
first 90 minutes of the *cash* session (09:30-11:00 ET), not a pre-market
window. ``diagnostics.run_data_diagnostics`` verifies this from the data.

Costs
-----
``round_turn_cost`` is charged once per contract per round turn and is
subtracted from ``pnl_per_contract``. It is a fixed dollar amount: it does
not scale with order size, so it says nothing about market impact at the
~40-contract sizes the 2% risk regime reaches.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

import pandas as pd

__all__ = [
    'Config', 'SimFlags', 'LEGACY_FLAGS', 'BacktestResult',
    'position_size', 'run_backtest', 'replace',
]


@dataclass(frozen=True)
class SimFlags:
    """Toggles for simulation conventions that materially change results.

    Defaults are the corrected conventions. ``LEGACY_FLAGS`` below flips
    every one of them back to what the original notebook did.
    """

    #: Fill a stop/target at the bar OPEN when the bar opens through the
    #: level, instead of at the level itself. Filling at the level through
    #: a gap is unambiguously optimistic.
    gap_aware_fills: bool = True

    #: Walk every bar in the feed, including overnight Globex bars. When
    #: False only bars inside ``window_start..trade_end`` are visible, so
    #: an overnight stop-out is never observed at its true crossing.
    scan_overnight_bars: bool = True

    #: Treat ``window_end`` as exclusive. When False the ``window_end`` bar
    #: both extends the breakout range and is itself tradable, which lets
    #: the bar's own high define the level it would have to break.
    exclusive_window_end: bool = True

    #: Forbid entries on the session's final bar. When False a position can
    #: be opened on the last bar and is force-closed at that same bar's
    #: close, booking a guaranteed round-turn cost and nothing else.
    block_last_bar_entry: bool = True

    #: Exit a max-hold long at the last bar of its final session. When
    #: False it exits at the first bar of that session instead.
    max_hold_exit_at_close: bool = True

    #: Map bars after the daily maintenance halt to the next session (CME
    #: convention). When False the plain calendar date is used.
    cme_trading_day: bool = True

    #: Mark equity to market at every session close, including open
    #: positions. When False equity only moves when a trade is realised,
    #: hiding all open-position drawdown.
    daily_mark_to_market: bool = True

    #: Round position size down to at least one contract. When True a
    #: trade is taken even if risk and margin both say zero.
    min_one_contract: bool = False

    #: Require at least one prior session before trading. Reproduces the
    #: original notebook's inert ``cond1..cond4`` filter gate, whose only
    #: real effect was to suppress trading on the sample's first day.
    require_prev_session: bool = False


#: Conventions of the original notebook, which produced the results
#: published in the README on ``main``.
LEGACY_FLAGS = SimFlags(
    gap_aware_fills=False,
    scan_overnight_bars=False,
    exclusive_window_end=False,
    block_last_bar_entry=False,
    max_hold_exit_at_close=False,
    cme_trading_day=False,
    daily_mark_to_market=False,
    min_one_contract=True,
    require_prev_session=True,
)


@dataclass(frozen=True)
class Config:
    point_value: float = 20.0          # $ per NQ index point
    tick_size: float = 0.25            # index points per tick
    tick_value: float = 5.0            # $ per tick (tick_size * point_value)
    stop_dollars: float = 2_000.0      # per contract -> 100 index points
    target_dollars: float = 4_000.0    # per contract -> 200 index points
    initial_capital: float = 1_000_000.0
    margin_per_contract: float = 23_282.57  # static present-day IB NQ margin
    margin_buffer: float = 0.15
    risk_pct: float = 0.01
    #: All-in cost per side per contract. $0.85 is IB's broker commission
    #: alone; all-in (broker + exchange + NFA + clearing) is ~$2.50.
    commission_per_side: float = 2.50
    slippage_ticks_per_side: float = 1.5
    #: Number of sides slippage is charged on. Should be 2 for a round
    #: turn; the original notebook charged 1 despite documenting 2.
    slippage_sides: int = 2
    window_start: dt.time = dt.time(8, 30)
    window_end: dt.time = dt.time(10, 0)
    trade_start: dt.time = dt.time(10, 0)
    trade_end: dt.time = dt.time(14, 30)
    regular_start: dt.time = dt.time(9, 30)
    regular_end: dt.time = dt.time(16, 0)   # daily Globex maintenance halt
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
        """Total $ cost per contract per round turn (fees + slippage)."""
        return (2 * self.commission_per_side
                + self.slippage_sides * self.slippage_ticks_per_side * self.tick_value)

    def __post_init__(self):
        if self.entry_mode not in ('stop', 'close'):
            raise ValueError(f"entry_mode must be 'stop' or 'close', got {self.entry_mode!r}")


#: Cost and fill conventions of the original notebook.
LEGACY_CONFIG_KWARGS = dict(commission_per_side=0.85, slippage_sides=1, entry_mode='close')


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series          # equity indexed by trading day
    config: Config
    flags: SimFlags


def position_size(equity: float, stop_pts: float, config: Config,
                  flags: SimFlags = SimFlags()) -> int:
    """Contracts as min(risk budget, margin cap after buffer)."""
    floor = 1 if flags.min_one_contract else 0
    if equity <= 0:
        return floor
    stop_pts = max(stop_pts, 1e-9)
    n_risk = (config.risk_pct * equity) / (stop_pts * config.point_value)
    n_margin = equity / (config.margin_per_contract * (1 + config.margin_buffer))
    return max(floor, int(min(n_risk, n_margin)))


def _trading_day(ts: pd.Timestamp, config: Config, flags: SimFlags) -> dt.date:
    """Map a bar to the trading day whose session it feeds into.

    Under CME convention a bar strictly after the daily maintenance halt
    belongs to the *next* calendar date's session, so a bar stamped Monday
    17:00 is part of Tuesday. The bar labelled exactly ``regular_end`` is
    still part of Monday: the halt empties the following buckets, so the
    next bar carrying data is the first one strictly after it.
    """
    d = ts.date()
    if flags.cme_trading_day and ts.time() > config.regular_end:
        return d + dt.timedelta(days=1)
    return d


def run_backtest(df_5min: pd.DataFrame, config: Config,
                 flags: SimFlags = SimFlags()) -> BacktestResult:
    df = df_5min.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['time'] = df['date'].dt.time

    if not flags.scan_overnight_bars:
        # The original notebook filtered the feed to session hours before
        # running, so overnight bars were never examined at all.
        df = df[(df['time'] >= config.window_start) & (df['time'] <= config.trade_end)].copy()

    df['trading_day'] = df['date'].map(lambda ts: _trading_day(ts, config, flags))
    if df.empty:
        raise ValueError('No bars in the input data.')

    trading_days = sorted(df['trading_day'].unique())
    day_index = {day: i for i, day in enumerate(trading_days)}
    first_trading_day = trading_days[0]

    session_mask = (df['time'] >= config.window_start) & (df['time'] <= config.trade_end)
    session_close_bar = df[session_mask].groupby('trading_day')['date'].max().to_dict()

    trades: list[dict] = []
    day_marks: list[tuple] = []
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
        if not flags.daily_mark_to_market:
            # Equity only moves when a trade is realised.
            day_marks.append((exit_day, account_value))

    for row in df.itertuples(index=False):
        row_timestamp = row.date
        row_trading_day = row.trading_day
        row_time = row.time
        is_last_bar = session_close_bar.get(row_trading_day) == row_timestamp

        if current_trading_day is None:
            current_trading_day = row_trading_day
        if row_trading_day != current_trading_day:
            # Shorts must never survive into the next trading day, even if
            # a data gap means the session had no recognised closing bar.
            if open_trade is not None and open_trade['direction'] == 'short':
                close_trade(prev_row_timestamp, prev_row_close, 'eod_short', current_trading_day)
            current_trading_day = row_trading_day
            daily_trade_count = 0
            window_high = None
            window_low = None
            window_ready = False

        if flags.exclusive_window_end:
            in_window = config.window_start <= row_time < config.window_end
        else:
            in_window = config.window_start <= row_time <= config.window_end
        if in_window:
            window_high = row.high if window_high is None else max(window_high, row.high)
            window_low = row.low if window_low is None else min(window_low, row.low)
        if not window_ready and row_time >= config.window_end and window_high is not None:
            window_ready = True

        # ---- exits (never on the entry bar: intra-bar sequencing from
        # OHLC alone is unknowable) ----
        exited_this_bar = False
        if open_trade is not None and row_timestamp > open_trade['entry_time']:
            exit_price = None
            exit_reason = None
            days_in_trade = day_index[row_trading_day] - day_index[open_trade['entry_day']] + 1
            stop = open_trade['stop']
            target = open_trade['target']
            gap = flags.gap_aware_fills
            if open_trade['direction'] == 'long':
                if gap and row.open <= stop:
                    exit_price, exit_reason = row.open, 'stop'
                elif row.low <= stop:
                    exit_price, exit_reason = stop, 'stop'
                elif gap and row.open >= target:
                    exit_price, exit_reason = row.open, 'target'
                elif row.high >= target:
                    exit_price, exit_reason = target, 'target'
                elif days_in_trade >= config.max_long_hold_days and (
                        is_last_bar or not flags.max_hold_exit_at_close):
                    exit_price, exit_reason = row.close, 'max_hold'
            else:
                if gap and row.open >= stop:
                    exit_price, exit_reason = row.open, 'stop'
                elif row.high >= stop:
                    exit_price, exit_reason = stop, 'stop'
                elif gap and row.open <= target:
                    exit_price, exit_reason = row.open, 'target'
                elif row.low <= target:
                    exit_price, exit_reason = target, 'target'
                elif is_last_bar:
                    exit_price, exit_reason = row.close, 'eod_short'
            if exit_price is not None:
                close_trade(row_timestamp, exit_price, exit_reason, row_trading_day)
                exited_this_bar = True

        # ---- entries ----
        allowed_bar = not (flags.block_last_bar_entry and is_last_bar)
        session_ok = not (flags.require_prev_session and row_trading_day == first_trading_day)
        if (open_trade is None and not exited_this_bar and window_ready
                and daily_trade_count < config.max_trades_per_day
                and config.trade_start <= row_time <= config.trade_end
                and allowed_bar and session_ok):
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
                contracts = position_size(account_value, stop_pts, config, flags)
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
                        'risk_pct': risk_dollars / account_value * 100 if account_value else float('nan'),
                    }
                    daily_trade_count += 1

        if is_last_bar and flags.daily_mark_to_market:
            day_marks.append((row_trading_day, account_value + unrealized(row.close)))

        prev_row_timestamp = row_timestamp
        prev_row_close = row.close

    if open_trade is not None:
        close_trade(prev_row_timestamp, prev_row_close, 'force_close', current_trading_day)

    if flags.daily_mark_to_market:
        # A force_close on the day of the last recorded mark means that
        # mark was a pre-close unrealised snapshot; replace it rather than
        # stacking a second entry for the same day.
        if day_marks and day_marks[-1][0] == current_trading_day:
            day_marks[-1] = (current_trading_day, account_value)
        else:
            day_marks.append((current_trading_day, account_value))

    equity = pd.Series({pd.Timestamp(day): value for day, value in day_marks}, name='equity')
    equity.index.name = 'date'
    equity = equity.sort_index()
    return BacktestResult(trades=pd.DataFrame(trades), equity=equity,
                          config=config, flags=flags)
