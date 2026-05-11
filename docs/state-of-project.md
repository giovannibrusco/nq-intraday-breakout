# State of Project — Resume Brief

Ultimo update: 2026-05-11 EOD

## Repo correlati

- **nq-intraday-breakout-v2** (questo): backtest engine, fixture generation, firm rules canonical
- **prop-evaluator** (`~/Desktop/prop-evaluator/`): simulation engine, eval/PA modeling

## Stato commits

- nq-intraday-breakout-v2: `docs/firms/apex_25k_eod.yaml` canonical Apex EOD, source-cited
- prop-evaluator @ **v0.4 part 1**: 126 test verdi, pushato su GitHub

## Numeri canonici validati — Phase E (TP=82, SL=88) on Apex 25K Eval @ 30d

| Strumento | P(pass) Markov | P(pass) block | Note |
|---|---|---|---|
| NQ | **47.6%** | 43.6% | insensibile all'orizzonte 30→180d |
| MNQ | **0.2%** | 5.2% | timeout-dominated; sparsity bias warning |

**Conclusioni**:
- Apex 25K Eval con Phase E NQ = coin flip. Il blow del trader (1 maggio) era nella regione mediana, non left tail.
- MNQ su Apex 25K Eval = dead end strutturale (PnL troppo piccolo per accumulare $1500 in 30d).

## Lessons learned codificate (CLAUDE.md di prop-evaluator)

1. **Source provenance**: ogni campo numerico in firm config richiede `# Fonte: <URL>` inline
2. **Predictions vs measurements**: ipotesi guidano priorità di misurazione, non sostituiscono misurazioni
3. **Block bootstrap sparsity bias**: se active_day_rate < 60%, usa Markov come primary; warning automatico se |delta block-Markov| > 10pp

## Prossimo step pianificato: Topstep 50K validation

Workflow:
1. Estrazione regole Topstep 50K via **Claude in Chrome** (prompt salvato in conversazione 2026-05-11)
2. Canonical YAML in `docs/firms/topstep_50k.yaml` (questo repo)
3. Migrazione config in prop-evaluator
4. Simulation Phase E + confronto P(pass) Apex vs Topstep

Tempo stimato: ~1h. Valore: stress test schema (DLL terminates, intraday trailing) + alternative reale ad Apex per il trader.

## Alternative pianificate (in ordine)

- **B. Apex 50K/100K** (sensibilità size): 30 min, less methodological
- **C. Ingestion adapter** (per qualsiasi CSV/parquet): 2-3h, sblocca app generica
- **D. PA simulator** (tier-based DLL, consistency, payouts): 3-4h, post-Eval economy

## Come riprendere

- **Discussione strategica / analisi numeri**: nuova session in `nq-intraday-breakout-v2/` (Opus 4.7 high o Sonnet medium), paste questo file + "continuiamo da qui"
- **Code work**: nuova session in `~/Desktop/prop-evaluator/` (Sonnet medium), paste questo file + task specifico

## Open notes metodologiche (debiti futuri)

- Block bootstrap "fix corretto" (sample calendar days incl. zero-trade days) — code change per future
- PA simulation non implementata (richiede tier-based DLL state machine, consistency rule, payout schedule)
- Ingestion adapter non implementato — strategic milestone per "app generica"
- Apex lock_at/lock_value verificati via Claude in Chrome 2026-05-09; **reverifica annuale consigliata** (T&C cambiano)
