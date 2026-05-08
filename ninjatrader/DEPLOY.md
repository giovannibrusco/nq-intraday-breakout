# NinjaTrader 8 Deploy Guide — NQBreakoutApex25kEod

Strategia C# pronta per Apex 25K EOD aggressive (TP=82, SL=1500, 1 NQ).

---

## Prerequisiti

- **NT8 license**: free per sim, paid per live. Apex include lease NT8 nel suo data feed Rithmic
- **Apex account**: 25K EOD, eval $29.90 promo ($109 PA activation)
- **PC Windows 24/7**: meglio con UPS + internet ridondante
- **Timezone**: chart MUST be Eastern Time (US & Canada)

---

## Step-by-step

### 1. Install file in NT8

Copia `NQBreakoutApex25kEod.cs` in:

```
C:\Users\<TUO_USER>\Documents\NinjaTrader 8\bin\Custom\Strategies\NQBreakoutApex25kEod.cs
```

### 2. Compile

NT8 → menu `New` → `NinjaScript Editor` → premi **F5** (Compile).

Se ci sono errori, leggi output console. Comuni:
- Missing `using` → already incluso, dovrebbe ok
- Wrong NT8 version → questo codice è per NT8 (build 8.1.x+)

### 3. Setup chart

1. New Chart → instrument `NQ ##-##` (front month continuous)
2. Bar type: **5 Minute**
3. Timezone (Tools → Options → General): **(UTC-05:00) Eastern Time (US & Canada)**
4. Data range: ultimi 60 giorni minimo (per warmup BarsRequiredToTrade)

### 4. Backtest (Strategy Analyzer)

1. NT8 → `New` → `Strategy Analyzer`
2. Strategy: `NQBreakoutApex25kEod`
3. Instrument: NQ ##-##
4. Time period: 2015-01-01 → 2025-12-31 (10 anni)
5. Bars: 5 Minute
6. Run backtest

**Expected results (aggressive setup, no Apex limits applied):**
- Total trades: ~1500-2500
- Total PnL: $40K-80K (1 NQ size, no compounding)
- Profit factor: 1.3-1.6
- Max drawdown: $5K-15K

**Tolerance vs Python backtest**: ±5-10%. Differences attese:
- NT8 fill al next bar OPEN vs Python row.close (1-2 tick slip per trade)
- Cost model: NT8 default vs Python `make_cost_model_for_instrument` ($3.98 RT NQ)

### 5. Sim101 paper trading

1. Connect Apex sim account (Connections → Apex Sim or Sim101)
2. Apply strategy to live chart
3. Click **Enable** (NinjaScript will start receiving bars)
4. Run 1-2 settimane minimo, watch:
   - Order fills correctly
   - DLL breaker triggers if down -$500
   - EOD flatten at 16:55 ET
   - No overnight positions
5. Compare daily PnL to Strategy Analyzer prediction

### 6. Apex Eval (live)

1. Buy 25K EOD eval ($29.90 promo)
2. Connect via Rithmic credentials Apex
3. Deploy strategy on live eval account
4. Wait pass: target $1500 (~5-15 giorni mediana)
   - DLL trip $500 = fail eval, paghi nuovo eval ($29.90)
5. Pass eval → 7 giorni per attivare PA ($109)

### 7. Apex PA (live money)

1. Activate PA after pass
2. Connect strategy to PA account
3. Scaling level 0: 1 NQ max, $500 DLL
4. After cumulative profit ≥$1000 → scaling level 1: 2 NQ max
   - **Manuale**: aumenta `Contract Quantity` parametro a 2 in NT8 strategy params
5. After cumulative ≥$2000 → scaling level 2: 2 NQ max, $1250 DLL
   - **Manuale**: aumenta `Daily Loss Limit` parametro a 1250
6. Payout request dopo 5+ qualifying days (≥$100/giorno) e safety net OK

---

## Parametri NT8 (UI)

Quando applichi strategia, vedrai questi parametri editabili:

| Parametro | Default | Note |
|---|---|---|
| Target Points (NQ) | 82 | TP in points NQ. Aggressive 25K_eod = 82 |
| Stop Loss Points (NQ) | 1500 | SL in points. 1500 = effectively no SL (DLL è il vero stop) |
| Max Trades Per Day | 2 | Backtest cap |
| Contract Quantity (NQ) | 1 | 1 NQ start. Bump a 2 dopo $1K cumulativo PA |
| Daily Loss Limit ($) | 500 | Apex 25K_eod scaling level 0. Bump a 1250 dopo $2K cumulativo |
| Enable DLL Circuit Breaker | true | Lascia ON sempre |

---

## Safety checklist pre-live

- [ ] Backtest 10y completato, PnL ±5% vs Python
- [ ] Sim101 ≥1 settimana, no errori
- [ ] DLL breaker testato (forza loss su sim, verifica flatten)
- [ ] EOD flatten testato (verifica chiusura prima 16:59)
- [ ] No overnight positions in run sim (controlla journal NT8)
- [ ] Account balance check funziona (Apex feed potrebbe non popolare cashValue, fallback 0)
- [ ] PC uptime garantito (no Windows update mid-trading)
- [ ] Internet stable, backup connessione
- [ ] Apex account credentials saved + 2FA backup

---

## Monitoring durante live

Apri NT8 → **Control Center** → **Log** tab. Strategy stampa:
- Day rollover + start balance
- Opening range ready (high/low)
- Entry signals con TP/SL points
- DLL breaches + emergency flatten
- EOD flattens

Salva log giornaliero per audit. Apex potrebbe fare review se ti accusano di automation abuse (improbabile, automation è permesso sotto certe regole).

---

## Failure modes & recovery

| Problema | Sintomo | Fix |
|---|---|---|
| Connection drop mid-trade | Position open, no orders | NT8 Auto-reconnect; verify position su Apex web UI |
| DLL trip non triggers | Day PnL < -$500, position aperta | Verify `EnableDLLBreaker=true`; check Account.CashValue popolato |
| EOD flatten fail | Position rimane oltre 16:59 | Apex auto-flatten interviene + violation. Use NT8 SetSessionTemplate |
| Strategy crash | Errore C# runtime | Output → Log; restart strategy; review `OnBarUpdate` exceptions |
| PnL drift vs backtest | Live -10%+ vs prediction | Slippage + Apex fees + filtering. Re-validate dopo 50+ trades |

---

## Scaling roadmap (post-25K_eod success)

```
Mese 1-2:  25K_eod cycle 1 (~50 giorni). Net se passa ~$1,200
Mese 3:    Decisione bivio:
           A) Stack: 2× 25K_eod paralleli
              - Stesso strategy, 2 chart, 2 Apex account
              - 2× throughput, 2× DLL exposure
           B) Upgrade: 50K_eod ($119 activation, $73K/yr annualized)
              - Riusa stesso .cs, cambia ContractQty=2 (level 0 = 2 NQ)
              - DLL=$1000

Limite Apex: max 5 PA simultanee → A) può scalare a 5× 25K = ~$175K/yr
```

---

## File pronti

- `NQBreakoutApex25kEod.cs` — Strategy NinjaScript
- `DEPLOY.md` — questo file
