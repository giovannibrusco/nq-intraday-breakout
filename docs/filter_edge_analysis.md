# Phase 4 - Analisi dell'edge dei filtri di regime

Data del documento: 2026-04-23

Questa analisi sostituisce l'artefatto `cond1 = cond2 = cond3 = cond4 = True`
che era hard-coded nel notebook originale. Quattro filtri sono stati
progettati sulla base di priori quantistici plausibili, implementati in
`src/nqbreakout/strategy/filters.py`, e valutati sull'intero storico NQ a
5 minuti (2015-03-29 / 2025-04-04, 2520 giorni di trading, 1728 trade nel
backtest baseline).

Il processo di verifica confronta, per ciascun filtro, lo Sharpe ratio
annualizzato del backtest con il filtro attivo contro il baseline
(nessun filtro). La significativita statistica e misurata tramite un
block-bootstrap paired (1000 resample, block size 10 giorni,
`np.random.default_rng(42)`) sul differenziale di Sharpe. La regola di
accettazione e:

    retain <=> (p-value < 0.10) AND (sharpe_delta > 0)

Tutti i filtri usano dati rigorosamente precedenti all'ingresso
(`.shift(1)` sulla serie giornaliera), come verificato in
`tests/test_filters.py::test_compute_daily_features_no_lookahead`.

## Filtro 1 - Overnight gap / drift

**Regola finale.** Dato il gap overnight normalizzato da ATR(20) dei range
giornalieri precedenti:

- Rifiuta entrambe le direzioni se `|gap_atr| > 2.0`.
- Blocca long se `gap_pct < -0.30%` o `gap_atr < -1.5`.
- Blocca short simmetrici sul lato positivo (`gap_pct > +0.30%` o
  `gap_atr > +1.5`).

**Rationale quantistico.** Gap estremi sono associati tipicamente a news
macro o earnings mega-cap: il mercato riprezza in pochi minuti e l'opening
range (08:30..10:00) perde valore predittivo. Lato simmetrico: un gap
avverso alla direzione del breakout aumenta il rischio di falsa rottura.

**Risultato empirico.** Il filtro elimina 412 trade (-24%) ma abbassa lo
Sharpe a 0.810 contro il baseline 0.877. Il gap atr e per la maggior parte
dei giorni piccolo (media |gap_atr| ~ 0.5), quindi il trigger a 2.0 rigetta
essenzialmente solo giorni gia outlier, molti dei quali si sarebbero
chiusi in target.

## Filtro 2 - Percentile del range del giorno precedente

**Regola finale.** Classifica il range del giorno D-1 all'interno
dell'intera distribuzione dei range dei 60 giorni precedenti (finestra
rolling, escluso il giorno corrente). Rifiuta entrambe le direzioni se il
percentile > 0.75.

**Rationale quantistico.** Ipotesi "regime esaurito": dopo una giornata di
range eccezionalmente ampio, lo swing di ritorno tende a essere mean-reverting
invece che trend-continuativo. L'opening-range breakout e fondamentalmente
una strategia trend-following, quindi dovrebbe perdere edge in regime post-
esauriemento.

**Risultato empirico.** Il filtro elimina 505 trade (-29%) e **migliora lo
Sharpe a 1.026** (Delta = +0.148), ma il p-value del bootstrap e 0.382,
molto lontano dalla soglia 0.10. Il guadagno e "plausibile" ma non
sufficientemente separato dal rumore di campionamento per essere adottato
come parametro di produzione. E' il candidato migliore tra i quattro ed e il
primo da riconsiderare in un campionamento piu ampio o OOS.

## Filtro 3 - Trend giornaliero (EMA slope)

**Regola finale.** Segno della derivata prima dell'EMA-50 sulla serie di
chiusure giornaliere (source = NQ, fallback QQQ). Long consentiti solo
se `slope > 0`, short solo se `slope < 0`. Slope esattamente zero
(raro) blocca entrambe.

**Rationale quantistico.** Il breakout intraday e una strategia
direzionale: un filtro di regime che blocca le scommesse contro il trend
macro dovrebbe ridurre le perdite da "shakeout" contro-trend.

**Risultato empirico.** Il filtro e molto aggressivo: elimina 745 trade
(-43%) mantenendo solo i breakout allineati al trend multi-settimanale.
Sharpe scende a 0.834 (Delta = -0.043, p = 0.803). Il problema: i breakout
intraday contro-trend sono spesso i piu redditizi (reversal quality),
mentre quelli nel trend sono gia riprezzati dal mercato.

## Filtro 4 - Liquidita di sessione

**Regola finale.** Rapporto tra volume accumulato nella finestra
[08:30..10:00] del giorno corrente e la mediana rolling a 20 giorni della
stessa finestra nei giorni precedenti. Rifiuta entrambe le direzioni se
`ratio < 0.5`.

**Rationale quantistico.** Breakout su volume sottile tendono a fallire
(assenza di flusso istituzionale che sostenga il movimento).

**Risultato empirico.** Solo 25 trade eliminati (-1%). Lo Sharpe cala
leggermente (0.841, Delta = -0.036, p = 0.378). Il filtro e
sostanzialmente inoperante: la mediana del rapporto sul campione 10y e
vicina a 1.0 e la soglia 0.5 cattura pochi giorni estremi.

## Tabella riassuntiva

| Run           | Trades | Sharpe | Delta Sharpe | p-value | Verdetto |
|---------------|-------:|-------:|-------------:|--------:|----------|
| baseline      |  1728  | 0.877  |  +0.000      | -       | baseline |
| overnight_gap |  1316  | 0.810  |  -0.067      | 0.701   | drop     |
| prior_range   |  1223  | 1.026  |  +0.148      | 0.382   | drop     |
| trend         |   983  | 0.834  |  -0.043      | 0.803   | drop     |
| liquidity     |  1703  | 0.841  |  -0.036      | 0.378   | drop     |
| combined      |   528  | 0.796  |  -0.081      | 0.738   | info     |

Grafico di riferimento: `reports/phase4_filter_edge_equity.png` (sei
curve di equity sovrapposte: baseline + 4 individuali + combined).
Dati numerici: `reports/phase4_filter_edge.csv`.

## Decisione finale

**Nessun filtro soddisfa la soglia di significativita p < 0.10**. In
`configs/strategy_default.yaml` tutti e quattro i toggle restano a `false`.
Le soglie sono comunque esposte e configurabili per permettere
esplorazioni successive senza ri-rilascio di codice.

## Caveat e interpretazione onesta

- **Assenza di look-ahead.** Tutte le feature sono lagged via `.shift(1)`
  prima di essere usate per decisioni di ingresso; il test
  `test_compute_daily_features_no_lookahead` verifica che una mutazione
  dell'OHLC del giorno T non alteri `gap_pct`, `range_pctile` o
  `ema_slope_sign` del giorno T (cambia solo `session_vol` che e nota
  entro le 10:00, prima dell'ingresso).
- **Fit in-sample.** L'analisi e stata condotta sull'intero storico 10y
  senza holdout OOS. Le soglie (0.30%, 1.5 ATR, 75th pctile, 0.5x vol)
  sono state scelte *prima* di guardare ai risultati: non c'e over-fitting
  di parametri, ma l'impossibilita di confermare l'edge su OOS rende
  ragionevole la decisione di non abilitare nulla in default.
- **Potere statistico.** 2520 giorni di trading producono circa ~252
  blocchi di 10 giorni indipendenti. Per uno scarto di Sharpe di 0.15 il
  potere del bootstrap pagato e limitato: il test e conservativo e
  potrebbe non rilevare edge genuini ma piccoli.
- **Candidato per Phase 6.** Il filtro `prior_range` e il solo con
  sharpe_delta > 0 e con rationale robusto; andrebbe rivalutato con:
  a) walk-forward rolling invece di significance globale; b) un
  quantile path-adaptive (0.80? 0.85?) in cross-validation.
