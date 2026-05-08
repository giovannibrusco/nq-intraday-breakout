# Cost model NQ Breakout

## Assumption esplicite

Ogni trade genera quattro componenti di costo:

1. **Fee round-trip** ($1.70): IB clearing $0.85 per leg, due leg per round trip.
2. **Entry slippage** (1.5 tick = $7.50): il segnale scatta su breakout della
   opening range, l'ordine viaggia come market-on-close della barra breakout.
   Un tick e mezzo e' conservativo ma ragionevole per NQ liquid durante la
   sessione regolare.
3. **Exit slippage**: varia in base al motivo di chiusura.
4. **Gap penalty** (opzionale): se il gap overnight supera `gap_trigger_ticks`
   si aggiunge `gap_slippage_ticks * tick_value` al costo di entrata del
   giorno successivo (Phase 4).

## Tabella costi per `exit_reason`

| exit_reason    | Exit slippage (ticks) | Totale (entry 1.5 + fee $1.70) |
| -------------- | --------------------- | ------------------------------ |
| `target`       | 0.0 (resting limit)   | $9.20                          |
| `stop`         | 1.5 (stop-market)     | $16.70                         |
| `eod`          | 1.0 (market chiusura) | $14.20                         |
| `max_hold`     | 1.0 (= eod)           | $14.20                         |
| `force_close`  | 1.0 (= eod)           | $14.20                         |

Giustificazione assunzioni:

- **Target = 0 tick slippage**: un ordine limit al livello target e' gia' in
  book prima che il prezzo arrivi. Fill ottimistico ma standard per strategie
  quantitative con target fisso.
- **Stop = 1.5 tick slippage**: stop-market in condizioni di stress. Stesso
  valore dell'entrata per simmetria e prudenza.
- **EOD = 1.0 tick slippage**: chiusura a mercato alle 16:00 ET, liquidita'
  generalmente buona ma non ottimale. Un tick e' un compromesso ragionevole.

## Confronto legacy vs new

| Modello         | Target  | Stop    | EOD     |
| --------------- | ------- | ------- | ------- |
| `LegacyCostModel` | $9.20 | $9.20   | $9.20   |
| `CostModel`     | $9.20   | $16.70  | $14.20  |

Il legacy model applicava lo slippage una sola volta per round trip
(`SLIPPAGE_PER_SIDE_TICKS * SLIPPAGE_PER_TICK_VALUE = 1.5 * 5 = $7.50`)
indipendentemente dal motivo di uscita, nonostante il nome `_PER_SIDE_TICKS`.
Il nuovo modello corregge questa asimmetria: i target hit beneficiano del
fill a resting limit, gli stop hit pagano il mercato pieno.

## Sensitivity flag `--pessimistic-target`

Per scenari di stress o backtesting conservativo, si puo' applicare 0.5 tick
di slippage anche al target (partial fill, order book thin):

```python
pessimistic = CostModel(target_slippage_ticks=0.5)
# total_round_trip_cost("target") -> $9.20 + 0.5 * $5 = $11.70
```

Phase 4 esporrera' questo parametro come flag CLI `--pessimistic-target`.

## Parametri `CostModel` (defaults)

```python
fee_per_leg_usd       = 0.85   # IB clearing, NQ
entry_slippage_ticks  = 1.5
stop_slippage_ticks   = 1.5
target_slippage_ticks = 0.0
eod_slippage_ticks    = 1.0
gap_slippage_ticks    = 0.0    # attivato in Phase 4
gap_trigger_ticks     = 4.0
tick_size             = 0.25
tick_value            = 5.00
```
