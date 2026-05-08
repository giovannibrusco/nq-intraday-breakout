// =============================================================================
// NQBreakoutApex25kEod.cs
//
// NinjaTrader 8 Strategy — NQ intraday opening-range breakout, long-only.
// Designed for Apex Trader Funding 25K EOD account, "aggressive" setup.
//
// Source of truth (Python backtest):
//   - scripts/phase11e_full_grid_backtest.py (TP × SL grid)
//   - src/nqbreakout/strategy/core.py (run_backtest engine)
//   - configs/strategy_default.yaml (session times)
//   - reports/phase11_summary/operational_setup.csv (25K_eod aggressive row)
//
// Operational params (25K_eod aggressive, MAE-corrected):
//   TP = 82 NQ points  (= 328 ticks @ 0.25 tick size)
//   SL = 25 NQ points (= 100 ticks; aligned to DLL distance, DLL fires first server-side)
//   Max 2 trades/day, long-only
//   1 NQ contract (= 10 MNQ equivalent, $20/pt exposure)
//   Session: opening range 08:30-10:00 ET, trade 10:00-14:30 ET, flatten 16:55 ET
//
// Apex 25K_eod scaling level 0 hard limits (PA phase):
//   Daily Loss Limit: $500  → emergency flatten if day PnL <= -$500
//   Max contracts mini: 1   → 1 NQ
//   Trailing drawdown: $1000 below EOD high water mark (Apex tracks server-side)
//
// IMPORTANT pre-deployment setup in NinjaTrader 8:
//   1. Chart timezone MUST be set to "(UTC-05:00) Eastern Time (US & Canada)"
//      (Tools → Options → General → Time zone)
//   2. Chart instrument: NQ ##-## (front month continuous)
//   3. Chart bars: 5 Minute
//   4. Connect via Rithmic (provided by Apex)
//   5. Test on Sim101 paper account first (Strategy Analyzer + live sim)
//   6. Compare PnL vs Python backtest before live PA deploy
//
// Known parity gaps vs Python backtest:
//   - NT8 entry fills at next bar OPEN (not breakout-bar CLOSE). ~1-2 tick slip
//     per trade, expected to wash out over 250+ trades/yr
//   - SetStopLoss/SetProfitTarget use managed bracket OCO (NT8 native)
//   - DLL circuit breaker checks Account.CashValue between bars; sub-bar swings
//     above DLL threshold are not caught (Apex platform also enforces)
//
// Deployment sequence:
//   1. Paste this file into NinjaTrader 8:
//      Documents\NinjaTrader 8\bin\Custom\Strategies\NQBreakoutApex25kEod.cs
//   2. NT8 → Tools → New NinjaScript → Strategy → Compile (F5)
//   3. Strategy Analyzer → load this strategy → backtest 2015-2025 NQ data
//   4. Verify PnL roughly matches Python backtest (tolerance ±5%)
//   5. Sim101 deploy 1-2 weeks → verify live execution
//   6. Apex eval account → deploy → pass eval (target $1500, ~5-15 days)
//   7. Apex PA → activate ($109 fee) → start collecting payouts
// =============================================================================

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.SuperDom;
using NinjaTrader.Gui.Tools;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.Core.FloatingPoint;
using NinjaTrader.NinjaScript.Indicators;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class NQBreakoutApex25kEod : Strategy
    {
        // ====================== Operational state ======================
        private double windowHigh;
        private double windowLow;
        private bool   windowReady;
        private int    dailyTrades;
        private DateTime currentDay;
        private double dayStartBalance;
        private bool   dayBalanceCaptured;  // audit A3: lazy baseline flag
        private bool   dayDLLBreached;
        private int    lastEntryBar;

        // Times-of-day (ET, chart MUST be set to America/New_York)
        private TimeSpan windowStartTime = new TimeSpan(8, 30, 0);
        private TimeSpan windowEndTime   = new TimeSpan(10, 0, 0);
        private TimeSpan tradeStartTime  = new TimeSpan(10, 0, 0);
        private TimeSpan tradeEndTime    = new TimeSpan(14, 30, 0);
        private TimeSpan eodFlattenTime  = new TimeSpan(16, 55, 0);  // 4 min before Apex 16:59 hard cutoff

        // Entry signal name (used by SetStopLoss/SetProfitTarget binding)
        private const string EntrySignal = "BreakoutLong";

        // ====================== NinjaScript lifecycle ======================
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                          = "NQ opening-range breakout, Apex 25K EOD aggressive (TP82/SL25=DLL)";
                Name                                 = "NQBreakoutApex25kEod";
                Calculate                            = Calculate.OnBarClose;
                EntriesPerDirection                  = 1;     // 1 long open at a time; multi-trade controlled by dailyTrades
                EntryHandling                        = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy         = true;
                ExitOnSessionCloseSeconds            = 60;
                IsFillLimitOnTouch                   = false;
                MaximumBarsLookBack                  = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution                  = OrderFillResolution.Standard;
                Slippage                             = 0;
                StartBehavior                        = StartBehavior.WaitUntilFlat;
                TimeInForce                          = TimeInForce.Day;
                TraceOrders                          = false;
                RealtimeErrorHandling                = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling                   = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade                  = 20;
                // Removed: IsInstantiatedOnEachOptimizationIterator (not present in
                // newer NT8 versions; only relevant for Strategy Analyzer optimization,
                // not live trading).

                // === User-configurable parameters (defaults = 25K_eod aggressive) ===
                TargetPoints     = 82;
                StopLossPoints   = 25;      // Phase E rev: aligned to DLL distance (25pt × $20/pt = $500 = DLL)
                MaxTradesPerDay  = 2;
                ContractQty      = 1;       // 1 NQ
                DailyLossLimit   = 500.0;   // Apex 25K_eod scaling level 0 DLL ($)
                EnableDLLBreaker = true;
            }
            else if (State == State.Configure)
            {
                // No additional data series needed — strategy operates on the chart's
                // primary series. User must set chart to 5-minute bars before deploy.
            }
            else if (State == State.DataLoaded)
            {
                ResetDailyState(forceReset: true);
            }
        }

        protected override void OnBarUpdate()
        {
            // Skip historical bars before strategy start window
            if (CurrentBar < BarsRequiredToTrade) return;

            // NT8 Time[0] = bar END timestamp, in chart timezone (must be ET)
            DateTime barTime  = Time[0];
            DateTime barDate  = barTime.Date;
            TimeSpan tod      = barTime.TimeOfDay;

            // ============ Day rollover ============
            if (barDate != currentDay)
            {
                ResetDailyState(forceReset: false);
                currentDay = barDate;
            }

            // ============ Lazy day-start balance baseline (audit A3) ============
            // Captured at first bar of RTH (>= 08:30 ET) where account feed returns
            // non-zero. Anchors DLL baseline to start-of-trading-session, not load-time
            // (which fires before Rithmic feed is ready → returns 0 → spurious DLL trips).
            if (!dayBalanceCaptured && tod >= windowStartTime)
            {
                double bal = GetAccountBalance();
                if (bal > 0)
                {
                    dayStartBalance    = bal;
                    dayBalanceCaptured = true;
                    Print(string.Format("{0} Day start balance baseline: {1:C}", barTime, dayStartBalance));
                }
            }

            // ============ DLL circuit breaker (Apex 25K_eod $500) ============
            // Audit Sprint 1 Day 1 fix (A1): no longer gated on Position != Flat.
            // Previous bug: stop fill at -$480 → flat → next entry → loss past -$500.
            // Fix: evaluate DLL every bar; set breached flag in BOTH open/flat cases.
            if (EnableDLLBreaker)
            {
                double currentBalance = GetAccountBalance();
                if (currentBalance > 0)  // skip if feed glitch (returns 0 when not ready)
                {
                    double dayPnL = currentBalance - dayStartBalance;
                    if (dayPnL <= -DailyLossLimit)
                    {
                        if (!dayDLLBreached)
                        {
                            Print(string.Format("{0} DLL breached: dayPnL={1:C} <= -{2:C}.",
                                barTime, dayPnL, DailyLossLimit));
                        }
                        dayDLLBreached = true;
                        if (Position.MarketPosition != MarketPosition.Flat)
                        {
                            Print(string.Format("{0} Emergency flatten.", barTime));
                            ExitLong("DLL_breaker", EntrySignal);
                        }
                        return;
                    }
                }
            }

            // After DLL breach: block all new entries for the rest of the day
            if (dayDLLBreached) return;

            // ============ EOD force flatten (before Apex 16:59 cutoff) ============
            if (tod >= eodFlattenTime && Position.MarketPosition == MarketPosition.Long)
            {
                Print(string.Format("{0} EOD flatten ({1}).", barTime, eodFlattenTime));
                ExitLong("EOD_flatten", EntrySignal);
                return;
            }

            // After EOD time: no new entries
            if (tod >= eodFlattenTime) return;

            // ============ Build opening range (08:30-10:00 ET) ============
            // Audit fix (A2): inclusive on both ends to match Python core.py:316
            //   `config.window_start <= row_time <= config.window_end`
            // The 10:00 bar feeds OR; entry on same bar is naturally blocked because
            // High[0] cannot exceed windowHigh that just absorbed it.
            if (tod >= windowStartTime && tod <= windowEndTime)
            {
                if (windowHigh == double.MinValue) windowHigh = High[0];
                else                               windowHigh = Math.Max(windowHigh, High[0]);

                if (windowLow == double.MaxValue)  windowLow = Low[0];
                else                               windowLow = Math.Min(windowLow, Low[0]);
            }

            // Window ready when bar time crosses 10:00 ET and we have valid range
            if (!windowReady && tod >= windowEndTime && windowHigh != double.MinValue)
            {
                windowReady = true;
                Print(string.Format("{0} Opening range ready: H={1:F2} L={2:F2}",
                    barTime, windowHigh, windowLow));
            }

            // ============ Entry: long-only breakout above window high ============
            if (Position.MarketPosition == MarketPosition.Flat
                && windowReady
                && dailyTrades < MaxTradesPerDay
                && tod >= tradeStartTime && tod <= tradeEndTime  // audit A2: match Python `<=` inclusivity
                && CurrentBar > lastEntryBar         // prevent same-bar re-entry after exit
                && High[0] > windowHigh)
            {
                // Set bracket BEFORE entry so OCO is bound to fill
                // CalculationMode.Ticks → relative to entry fill price
                // Audit P2 fix: derive from TickSize instead of hardcoded 4.
                // Defensive against accidental MNQ/other-instrument deploy
                // (NQ tick_size=0.25 → mult=4; if chart ever uses different
                // instrument with different tick_size, math stays correct).
                int tickMultiplier = (int)Math.Round(1.0 / TickSize);
                SetProfitTarget(EntrySignal, CalculationMode.Ticks, TargetPoints * tickMultiplier);
                SetStopLoss    (EntrySignal, CalculationMode.Ticks, StopLossPoints * tickMultiplier, false);

                EnterLong(ContractQty, EntrySignal);
                dailyTrades++;
                lastEntryBar = CurrentBar;

                Print(string.Format("{0} ENTRY long {1}NQ above OR_high={2:F2} (close={3:F2}). TP+{4}pt SL-{5}pt",
                    barTime, ContractQty, windowHigh, Close[0], TargetPoints, StopLossPoints));
            }
        }

        // ====================== Helpers ======================
        private void ResetDailyState(bool forceReset)
        {
            windowHigh         = double.MinValue;
            windowLow          = double.MaxValue;
            windowReady        = false;
            dailyTrades        = 0;
            dayDLLBreached     = false;
            lastEntryBar       = -1;
            dayStartBalance    = 0.0;
            dayBalanceCaptured = false;
            // Audit A3 fix: balance baseline NO LONGER captured here.
            // State.DataLoaded fires before Rithmic feed is ready → balance read = 0.
            // Captured lazily in OnBarUpdate at first bar where tod >= windowStartTime
            // AND GetAccountBalance() returns non-zero. Anchors DLL baseline to
            // start-of-RTH, matching what Apex tracks server-side.

            if (!forceReset)
            {
                Print(string.Format("{0} Day rollover. Awaiting first session bar for balance baseline.", Time[0]));
            }
        }

        private double GetAccountBalance()
        {
            // Returns current account cash value in USD; falls back to 0 if not connected
            try
            {
                if (Account != null)
                    return Account.Get(AccountItem.CashValue, Currency.UsDollar);
            }
            catch { /* ignore */ }
            return 0.0;
        }

        // ====================== UI parameters ======================
        [NinjaScriptProperty]
        [Range(1, 1000)]
        [Display(Name = "Target Points (NQ)", Order = 1, GroupName = "Strategy")]
        public int TargetPoints { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10000)]
        [Display(Name = "Stop Loss Points (NQ)", Order = 2, GroupName = "Strategy")]
        public int StopLossPoints { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Max Trades Per Day", Order = 3, GroupName = "Strategy")]
        public int MaxTradesPerDay { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "Contract Quantity (NQ)", Order = 4, GroupName = "Strategy")]
        public int ContractQty { get; set; }

        [NinjaScriptProperty]
        [Range(50, 5000)]
        [Display(Name = "Daily Loss Limit ($)", Order = 5, GroupName = "Apex Risk")]
        public double DailyLossLimit { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable DLL Circuit Breaker", Order = 6, GroupName = "Apex Risk")]
        public bool EnableDLLBreaker { get; set; }
    }
}
