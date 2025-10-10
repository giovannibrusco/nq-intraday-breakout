# NQ Intraday Breakout Strategy

## Overview
This project implements a fully automated **intraday breakout strategy** on the **Nasdaq (NQ) futures**, built in Python.  
The system identifies directional bias using the first 5-minute candle of the session (09:30–09:35) and opens trades accordingly.  
It features dynamic **stop loss** and **take profit** levels based on the morning range, **position management**, and complete **performance tracking**.

The goal of the project is to reproduce, analyze, and improve the logic of a well-known intraday breakout pattern through rigorous backtesting and robust statistical validation.

---

## Strategy Logic
- Data: 5-minute NQ futures candles (regular trading hours).  
- Entry: at **09:35**, in the direction of the 09:30 candle breakout.  
- Stop Loss: based on the high/low of the 09:30 candle.  
- Take Profit: proportional multiple of the stop distance.  
- Position sizing: dynamic, as a function of account equity and risk per trade.  
- Exit: when TP or SL are hit, or at the end of the session.  
- Benchmark: Buy & Hold vs. strategy performance.

---

## Quickstart
```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run backtest
python src/main.py
