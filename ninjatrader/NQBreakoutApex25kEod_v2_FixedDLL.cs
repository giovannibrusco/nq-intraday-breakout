// =============================================================================
// NQBreakoutApex25kEod_v2_FixedDLL.cs
//
// NinjaTrader 8 Strategy — NQ intraday opening-range breakout, long-only.
// Designed for Apex Trader Funding 25K EOD account, "aggressive" setup.
//
// FIX V2: Implementato PnL sintetico (Realized + Unrealized) per far 
// scattare correttamente il Daily Loss Limit (DLL) nel backtester storico,
// aggirando il bug di Account.CashValue assente in Strategy Analyzer.
//
// IMPORTANT pre-deployment setup in NinjaTrader 8:
//   1. Chart timezone MUST be set to "(UTC-06:00) Central Time (US & Canada)"
//      (Questo allinea le 08:30 CT del codice all'apertura RTH 09:30 ET del Nasdaq)
//   2. Chart instrument: NQ ##-## (front month continuous)
//   3. Chart bars: 5 Minute
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
    public class NQBreakoutApex25kEod_v2_FixedDLL : Strategy
    {
        // ====================== Operational state ======================
        private double windowHigh;
        private double windowLow;
        private bool   windowReady;
        private int    dailyTrades;
        private DateTime currentDay;
        private double dayStartBalance;
        private bool   dayBalanceCaptured;  
        private bool   dayDLLBreached;
        private int    lastEntryBar;
        
        // FIX: Variabile per tracciare i profitti/perdite chiusi nella giornata corrente
        private double dailyRealizedPnL;

        // Times-of-day (Assumendo grafico in Central Time CT)
        // 08:30 CT = 09:30 ET (Apertura Nasdaq)
        private TimeSpan windowStartTime = new TimeSpan(8, 30, 0);
        private TimeSpan windowEndTime   = new TimeSpan(10, 0, 0);
        private TimeSpan tradeStartTime  = new TimeSpan(10, 0, 0);
        private TimeSpan tradeEndTime    = new TimeSpan(14, 30, 0);
        private TimeSpan eodFlattenTime  = new TimeSpan(15, 55, 0);  // 15:55 CT = 16:55 ET

        private const string EntrySignal = "BreakoutLong";

        // ====================== NinjaScript lifecycle ======================
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                          = "NQ opening-range breakout, Apex 25K EOD aggressive - FIXED DLL";
                Name                                 = "NQBreakoutApex25kEod_v2_FixedDLL";
                Calculate                            = Calculate.OnBarClose;
                EntriesPerDirection                  = 1;     
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

                TargetPoints     = 82;
                StopLossPoints   = 1500;
                MaxTradesPerDay  = 2;
                ContractQty      = 1;       
                DailyLossLimit   = 500.0;   
                EnableDLLBreaker = true;
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade) return;

            DateTime barTime  = Time[0];
            DateTime barDate  = barTime.Date;
            TimeSpan tod      = barTime.TimeOfDay;

            // ============ Day rollover ============
            if (barDate != currentDay)
            {
                ResetDailyState(forceReset: false);
                currentDay = barDate;
            }

            // ============ DLL circuit breaker (Apex 25K_eod $500) FIXED ============
            // Valuta la somma tra PnL realizzato oggi e PnL latente della posizione aperta
            if (EnableDLLBreaker)
            {
                double unrealizedPnL = 0.0;
                if (Position.MarketPosition != MarketPosition.Flat)
                {
                    unrealizedPnL = Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, Close[0]);
                }

                double currentDayPnL = dailyRealizedPnL + unrealizedPnL;

                if (currentDayPnL <= -DailyLossLimit)
                {
                    if (!dayDLLBreached)
                    {
                        Print(string.Format("{0} DLL breached: dayPnL={1:C} <= -{2:C}.",
                            barTime, currentDayPnL, DailyLossLimit));
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

            if (dayDLLBreached) return;

            // ============ EOD force flatten ============
            if (tod >= eodFlattenTime && Position.MarketPosition == MarketPosition.Long)
            {
                Print(string.Format("{0} EOD flatten ({1}).", barTime, eodFlattenTime));
                ExitLong("EOD_flatten", EntrySignal);
                return;
            }

            if (tod >= eodFlattenTime) return;

            // ============ Build opening range ============
            if (tod >= windowStartTime && tod <= windowEndTime)
            {
                if (windowHigh == double.MinValue) windowHigh = High[0];
                else                               windowHigh = Math.Max(windowHigh, High[0]);

                if (windowLow == double.MaxValue)  windowLow = Low[0];
                else                               windowLow = Math.Min(windowLow, Low[0]);
            }

            if (!windowReady && tod >= windowEndTime && windowHigh != double.MinValue)
            {
                windowReady = true;
                Print(string.Format("{0} Opening range ready: H={1:F2} L={2:F2}", barTime, windowHigh, windowLow));
            }

            // ============ Entry: long-only breakout ============
            if (Position.MarketPosition == MarketPosition.Flat
                && windowReady
                && dailyTrades < MaxTradesPerDay
                && tod >= tradeStartTime && tod <= tradeEndTime  
                && CurrentBar > lastEntryBar         
                && High[0] > windowHigh)
            {
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
            
            // Azzeramento del PnL realizzato all'inizio del nuovo giorno
            dailyRealizedPnL   = 0.0;

            if (!forceReset)
            {
                Print(string.Format("{0} Day rollover. Resetting internal state.", Time[0]));
            }
        }

        // ============ FIX: Tracking realized PnL ============
        protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
        {
            // Quando la posizione viene chiusa, aggiungi il profit/loss al PnL realizzato di giornata
            if (position.MarketPosition == MarketPosition.Flat && SystemPerformance.AllTrades.Count > 0)
            {
                Trade lastTrade = SystemPerformance.AllTrades[SystemPerformance.AllTrades.Count - 1];
                
                // Controllo di sicurezza per sommare solo i trade del giorno corrente
                if (lastTrade.ExitTime.Date == currentDay)
                {
                    dailyRealizedPnL += lastTrade.ProfitCurrency;
                }
            }
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