# Regole Apex Trader Funding — New Products (2026)

**Data documento:** 2026-04-25
**Modello:** Apex "New Products" (lanciati 2026-03-01, sostituiscono i Legacy Products)
**Status verifica:** Dati estratti da `apextraderfunding.com` via browser session 2026-04-25 +
screenshot ufficiali `/Users/giovannibrusco/Desktop/Apex*.pdf`. Tutte le fonti URL inline per ogni sezione.

> **Nomenclatura ufficiale:** Apex non usa il termine "4.0". I prodotti nuovi sono
> **EOD Trailing Drawdown Accounts** e **Intraday Trailing Drawdown Accounts**.
> Il sistema legacy a 7 tier non è più acquistabile per nuovi account.

---

## 1. Architettura prodotti

Apex offre **2 linee di prodotto × 4 size × 2 fasi (Eval + PA)**.
Prezzi diversi per variante (EOD costa più dell'Intraday su tutto):

### Eval — full price / coupon promo

| Tier | Intraday Eval | Intraday Coupon | EOD Eval | EOD Coupon |
|------|---------------|-----------------|----------|------------|
| 25K  | $199          | $19.90          | $299     | $29.90     |
| 50K  | $249          | $24.90          | $299     | ~$29.90 (verificare) |
| 100K | $399          | $39.90          | $599     | $59.90     |
| 150K | $599          | $59.90          | $799     | $79.90     |

### PA — one-time activation fee

| Tier | Intraday PA | EOD PA |
|------|-------------|--------|
| 25K  | $89         | $109   |
| 50K  | **$69**     | $119   |
| 100K | **$119**    | $139   |
| 150K | $139        | $159   |

**Tutti i fee sono ONE-TIME, no subscription mensile.**

---

## 2. Evaluation Phase

### 2.1 Eval — specs comuni

| Tier | Profit Target | Max Drawdown | Min Days To Pass | Time Limit |
|------|--------------|--------------|------------------|------------|
| 25K  | $1,500       | $1,000       | 1                | 30 days    |
| 50K  | $3,000       | $2,000       | 1                | 30 days    |
| 100K | $6,000       | $3,000       | 1                | 30 days    |
| 150K | $9,000       | $4,000       | 1                | 30 days    |

### 2.2 Eval — max contracts (uguali entrambe varianti)

| Tier | Mini (NQ) | Micro (MNQ) |
|------|-----------|-------------|
| 25K  | 4         | 40          |
| 50K  | 6         | 60          |
| 100K | 8         | 80          |
| 150K | 12        | 120         |

Conversione: **10 micro = 1 mini** in posizione equivalente.

### 2.3 Eval — DLL (Daily Loss Limit)

| Tier | EOD Trail Eval | Intraday Trail Eval |
|------|---------------|---------------------|
| 25K  | $500          | **None**            |
| 50K  | $1,000        | **None**            |
| 100K | $1,500        | **None**            |
| 150K | $2,000        | **None**            |

**Intraday Eval NON ha DLL.** Solo trailing drawdown limit.

### 2.4 Eval — regole comuni

- **Consistency rule in Eval: NESSUNA.** Si può violare la 50% rule senza conseguenza.
- **No MAE rule, no 5:1 RR rule** (rimosse vs Legacy).
- **Reset Fee: N/A** — se l'eval fallisce, occorre acquistare nuova eval (nessun reset, nessun rebill automatico).
- **Scaling:** "Built-in for PA" (non si applica scaling in eval).
- **Flatten cutoff: 16:59 ET.**
- **No overnight, no weekend hold** (chiusura forzata 16:59 ET ogni giorno).

**Fonti:** screenshot Apex Rules.pdf + https://apextraderfunding.com/help-center/billing/evaluation-plan-fees-and-access-explained/

---

## 3. Performance Account (PA) Phase

### 3.1 PA — activation fees & specs comuni

| Tier | Activation Intraday | Activation EOD | PA Max Contracts | Max DD | Activation Deadline | Max Accounts |
|------|---------------------|----------------|------------------|--------|---------------------|--------------|
| 25K  | $89                 | $109           | 2 mini / 20 micro| $1,000 | 7 days from eval pass | 20 |
| 50K  | $69                 | $119           | 4 mini / 40 micro| $2,000 | 7 days              | 20 |
| 100K | $119                | $139           | 6 mini / 60 micro| $3,000 | 7 days              | 20 |
| 150K | $139                | $159           | 10 mini / 100 micro| $4,000| 7 days             | 20 |

> Nota: PA max contracts è la **cap massima**, ma lo Scaling tier-based limita
> i contratti effettivi disponibili in funzione del profit corrente (sezione 3.5).

### 3.2 PA — regole comuni

- **Consistency rule: 50%** (vs Legacy 30%) — nessun singolo giorno profittevole può superare il 50%
  della somma dei giorni profittevoli. Si applica solo al **passing check finale per payout request**,
  non blocca trading durante il PA.
- **Profit Split: 100% to trader** (vs Legacy 90/10 sopra threshold).
- **Max Payout Requests per PA lifetime: 6.** Dopo il 6° payout, PA chiuso, occorre nuova eval.
- **Inactivity Policy: YES** (sezione 3.7).
- **Reset Fee: N/A** (no reset disponibile sul PA dopo violazione drawdown).
- **Flatten cutoff: 16:59 ET, no overnight, no weekend.**

### 3.3 PA — Safety Net Formula

```
Safety Net = Starting Balance + Max Drawdown + $100
```

| Tier | Starting | Max DD | **Safety Net (= Min Balance to Request Payout)** |
|------|----------|--------|--------------------------------------------------|
| 25K  | $25,000  | $1,000 | $26,100 (request balance min: $26,600 = +$500 min payout) |
| 50K  | $50,000  | $2,000 | $52,100 (request min: $52,600) |
| 100K | $100,000 | $3,000 | $103,100 (request min: $103,600) |
| 150K | $150,000 | $4,000 | $154,100 (request min: $154,600) |

- **Safety Net = soglia minima balance per poter richiedere un payout.** Sotto quel livello, payout request non disponibile.
- **Safety Net resta in vigore per tutta la vita del PA**, non sparisce dopo il primo payout.
- **Non chiude l'account.** A chiudere è il Trailing Drawdown Threshold (= Safety Net − $100 dopo freeze, vedi sezione 3.4).

### 3.4 PA — Trailing Drawdown Freeze

- Il trailing drawdown threshold si "congela" quando arriva a `Starting Balance + $100`.
- Questo si verifica quando **EOD balance (per EOD Trail) o Peak Balance incl. unrealized (per Intraday Trail) raggiunge `Starting Balance + Max Drawdown + $100` = Safety Net level**.
- Esempio 50K EOD: threshold si ferma a $50,100; serve EOD balance di $52,100 per congelarlo.
- Una volta congelato, threshold non scende mai più.

**Differenza EOD vs Intraday Trail:**
- **EOD Trail:** threshold sale solo al close 16:59:59 ET (snapshot daily). Unrealized PnL non conta.
- **Intraday Trail:** threshold sale in real-time sul Peak Balance, che include unrealized PnL.
  Più stretto: trade in profit non chiuso può alzare il threshold e poi se il prezzo torna indietro
  causa violation.

### 3.5 PA — Scaling Levels (Built-in)

Tabelle identiche per EOD PA e Intraday PA. Aggiornamento Tier una volta al giorno (16:59:59 ET),
si applica alla sessione successiva.

**$25K PA:**
| Tier Level | Profit Range | Max Contracts | DLL |
|-----------|--------------|---------------|-----|
| 1 | $0 – $999    | 1 | $500   |
| 2 | $1,000 – $1,999 | 2 | $500   |
| 3 | $2,000 & up  | 2 | $1,250 |

**$50K PA:**
| Tier Level | Profit Range | Max Contracts | DLL |
|-----------|--------------|---------------|-----|
| 1 | $0 – $1,499  | 2 | $1,000 |
| 2 | $1,500 – $2,999 | 3 | $1,000 |
| 3 | $3,000 – $5,999 | 4 | $2,000 |
| 4 | $6,000 & up  | 4 | $3,000 |

**$100K PA:**
| Tier Level | Profit Range | Max Contracts | DLL |
|-----------|--------------|---------------|-----|
| 1 | $0 – $1,999  | 3 | $1,750 |
| 2 | $2,000 – $2,999 | 4 | $1,750 |
| 3 | $3,000 – $4,999 | 5 | $1,750 |
| 4 | $5,000 – $9,999 | 6 | $2,500 |
| 5 | $10,000 & up | 6 | $3,500 |

**$150K PA:**
| Tier Level | Profit Range | Max Contracts | DLL |
|-----------|--------------|---------------|-----|
| 1 | $0 – $1,999  | 4 | $2,500 |
| 2 | $2,000 – $2,999 | 5 | $2,500 |
| 3 | $3,000 – $4,999 | 7 | $2,500 |
| 4 | $5,000 – $9,999 | 10 | $3,000 |
| 5 | $10,000 & up | 10 | $4,000 |

**Note critiche:**
- "Max Contracts" sopra = mini equivalent (10 micro = 1 mini per cap).
- **Scaling Level reset al baseline (Level 1) dopo OGNI payout approvato** (il "profit" scende perché parte del balance esce).
- **DLL nel PA è tier-based per ENTRAMBE le varianti** (no DLL solo nell'eval Intraday).

**Fonti:** https://apextraderfunding.com/help-center/additional-helpful-items/scaling-levels-pa-explained/ + https://apextraderfunding.com/help-center/additional-helpful-items/daily-loss-limit-explained/

### 3.6 PA — Payout Ladder

Cap per request, sequenziale. **EOD PA e Intraday PA hanno tabelle DIVERSE.**

**EOD Trail PA Max Payouts:**

| # Payout | 25K | 50K | 100K | 150K |
|----------|-----|-----|------|------|
| 1 | $1,000 | $1,500 | $2,000 | $2,500 |
| 2 | $1,000 | $1,500 | $2,500 | $3,000 |
| 3 | $1,000 | $2,000 | $2,500 | $3,000 |
| 4 | $1,000 | $2,500 | $3,000 | $3,000 |
| 5 | $1,000 | $2,500 | $4,000 | $4,000 |
| 6 | $1,000 | $3,000 | $4,000 | $5,000 |
| **Lifetime cap** | **$6,000** | **$13,000** | **$18,000** | **$20,500** |

**Intraday Trail PA Max Payouts:**

| # Payout | 25K | 50K | 100K | 150K |
|----------|-----|-----|------|------|
| 1 | $1,000 | $1,500 | $2,000 | $2,500 |
| 2 | $1,000 | $2,000 | $2,500 | $3,000 |
| 3 | $1,000 | $2,500 | $3,000 | $3,000 |
| 4 | $1,000 | $2,500 | $3,000 | $4,000 |
| 5 | $1,000 | $3,000 | $4,000 | $4,000 |
| 6 | $1,000 | $3,000 | $4,000 | $5,000 |
| **Lifetime cap** | **$6,000** | **$14,500** | **$18,500** | **$21,500** |

- **Min payout per request: $500** (tutti i tier).
- Differenza Intraday vs EOD lifetime: tier 25K identici ($6k), 50K-150K l'Intraday cappa marginalmente più alto.

**Fonti:** https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-payouts/ + https://apextraderfunding.com/help-center/intraday-trailing-drawdown-accounts/intraday-trailing-drawdown-payouts/

### 3.7 PA — Qualifying Day Rule

**Definizione:** giorno di trading con **net daily profit ≥ Min Daily Profit per quel tier/variante**.

| Tier | EOD PA Min Daily Profit | Intraday PA Min Daily Profit |
|------|------------------------|------------------------------|
| 25K  | $100  | $100 |
| 50K  | $250  | $200 |
| 100K | $300  | $250 |
| 150K | $350  | $300 |

- **Per richiedere PRIMO payout:** servono **5 qualifying days** (non necessariamente consecutivi).
- **Per payout successivi:** servono altri **5 nuovi qualifying days dopo l'ultimo payout approvato**
  (counter resetta).
- **Frequency rule:** "5 trading days" = de facto 1 payout/settimana se si tradano 5 giorni profittevoli consecutivi.
- **No min trading days nell'Eval** — si può passare anche in 1 giorno.

### 3.8 PA — Inactivity Policy

- **Definizione "inactive":** mancata registrazione di **almeno 2 trading days con net profit ≥ $50** all'interno di una rolling window di 30 giorni di calendario.
- **Dormant phase:** dopo 15 giorni senza soddisfare la soglia, account flag dormant + notifica giornaliera.
- **Chiusura definitiva:** dopo 30 giorni di calendario consecutivi senza soddisfare la soglia, account chiuso permanentemente, non ripristinabile.
- **Si applica a:** Performance Accounts (Intraday + EOD). Non si applica all'Eval.

**Fonte:** https://apextraderfunding.com/help-center/billing/inactivity-policy-on-performance-accounts-pa/

---

## 4. Cost Model — NQ/MNQ Rithmic Fees

### 4.1 Fee per round-trip

| Symbol | Exchange | NFA | Clearing | Routing | Apex (NinjaTrader Free Lifetime) | **Total RT** |
|--------|----------|-----|----------|---------|----------------------------------|--------------|
| NQ     | $1.40    | $0.04 | $0.50  | $0.04   | $2.00                            | **$3.98**    |
| MNQ    | $0.40    | $0.04 | $0.18  | $0.04   | $0.36                            | **$1.02**    |

### 4.2 Slippage assumptions (split model, da `costs.py`)

| Componente | NQ ticks | NQ $ | MNQ ticks | MNQ $ |
|------------|----------|------|-----------|-------|
| Entry slippage | 1.5 | $7.50 | 1.5 | $0.75 |
| Stop slippage  | 1.5 | $7.50 | 1.5 | $0.75 |
| Target slippage| 0.0 | $0.00 | 0.0 | $0.00 |
| EOD slippage   | 1.0 | $5.00 | 1.0 | $0.50 |

### 4.3 Round-trip totale per exit reason

| Exit | NQ total | MNQ total |
|------|----------|-----------|
| Target | $3.98 + $7.50 + $0 = **$11.48** | $1.02 + $0.75 + $0 = **$1.77** |
| Stop   | $3.98 + $7.50 + $7.50 = **$18.98** | $1.02 + $0.75 + $0.75 = **$2.52** |
| EOD    | $3.98 + $7.50 + $5.00 = **$16.48** | $1.02 + $0.75 + $0.50 = **$2.27** |

> Nota update: i precedenti `DEFAULT_COST_MODEL` in `costs.py` usavano `fee_per_leg_usd=0.85`
> (= $1.70 RT) basato su "IB clearing per leg" da Phase 2. **Va aggiornato** a $3.98 RT NQ /
> $1.02 RT MNQ per Apex Rithmic.

---

## 5. Decisioni di Modellazione (per MC engine)

### 5.1 Eval simulation

- Sample N giorni con block bootstrap (block_size=10, default).
- Per ogni giorno: check intraday violation con proxy `balance + min(daily_pnl, 0)`, poi `update_eod`.
- Pass se `balance ≥ start + profit_target` E `days ≥ 1` E **(no consistency check in eval)**.
- Time limit: 30 trading days max.
- Se Intraday Trail eval: NESSUN DLL check (vedi 2.3).
- Se EOD Trail eval: check DLL su `daily_pnl < -DLL_amount`.

### 5.2 PA simulation (lifecycle completo)

Stato per simulazione:
```python
balance = starting_balance
profit_so_far = 0  # balance - starting_balance
qualifying_days_count = 0
payouts_taken = []  # list of $ amounts
inactivity_window = []  # rolling 30 days, qualifying-50 check
day_idx = 0
```

Per ogni giorno:
1. **Determine current scaling tier** in funzione di `profit_so_far`.
2. **Cap contracts** = `min(scaling_max, account.pa_max_contracts, risk_pct_cap, margin_cap)`.
3. **Check DLL** per quel tier.
4. **Sample daily PnL** dal historical (block bootstrap).
5. **Apply pnl** → update balance, profit_so_far, peak.
6. **Check trailing violation** (Intraday: real-time peak; EOD: peak EOD).
7. **Update qualifying_days** se `daily_pnl ≥ min_daily_profit_tier_variant`.
8. **Update inactivity_window** (rolling 30 cal days, count days with pnl ≥ $50).
9. **Trigger payout request** quando: `qualifying_days_count ≥ 5` AND `balance ≥ safety_net + min_payout`.
   - Payout amount = `min(payout_ladder[next_payout_idx], available_above_safety_net)`
   - Subtract from balance, increment payout_idx, reset qualifying_days_count.
10. **Stop conditions:**
    - `payouts_taken ≥ 6` → close PA, success
    - `trailing_violated` → close PA, fail
    - `inactivity_window` non soddisfa soglia per 30 giorni → close PA, inactivity
    - `day_idx > max_days` → timeout (es. max 252 trading days = 1 anno)

### 5.3 Lifecycle ROI

```
Cycle = Eval purchase → Eval pass (with prob P_eval) → PA activation → PA play to end
Expected payout per cycle = P_eval * E[total_payouts | pass_PA] - eval_cost - PA_activation_cost
ROI = expected_payout / cycle_time_days * 252  (annualized)
```

---

## 6. Caveats Aperti

1. **MC intraday proxy:** `balance + min(daily_pnl, 0)` non cattura MAE bar-level.
   - **Critico per Intraday Trail PA** (più stretto, peak include unrealized).
   - **Meno critico per EOD Trail PA** (snapshot solo a 16:59:59 ET).
   - **Phase upgrade futuro:** ricostruzione bar-level per Intraday.

2. **PA fee 100K verificata indirettamente:** $109/$139 stimati da pattern (50K = $89/$119, 150K = $139/$159).
   - Da confermare con screenshot 100K PA dedicato (PDF parziale).

3. **Min payout = $500 universal:** confermato da help-center.

4. **Promo prezzi eval coupon:** $19.90 etc — sono prezzi promo frequenti ma non garantiti permanenti.
   - Modello base con prezzo full-price; sensitivity analysis con promo.

5. **Rebill PA:** dopo 6 payouts, PA chiuso. Per "ricominciare" serve nuova eval ($199 full o $19.90 promo per 25K) + nuova activation ($89 Intraday / $109 EOD per 25K).

---

## 7. Mapping da Legacy 7-tier a New 4-tier

Per riferimento (compatibility con vecchi report):

| Legacy | New Equivalent |
|--------|----------------|
| apex_25k | apex_25k_intraday OR apex_25k_eod |
| apex_50k | apex_50k_intraday OR apex_50k_eod |
| apex_75k | **REMOVED** (no equivalent in New) |
| apex_100k | apex_100k_intraday OR apex_100k_eod |
| apex_150k | apex_150k_intraday OR apex_150k_eod |
| apex_250k | **REMOVED** |
| apex_300k | **REMOVED** |

Il vecchio `configs/apex_accounts.yaml` con 7 tier è da ritenersi **deprecato**.
Sostituito da nuovo schema 4 tier × 2 variants in Phase 9C.4.
