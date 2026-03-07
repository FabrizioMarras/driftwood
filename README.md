# 🌊 Driftwood

Automated trend-following trading system for BTC/USD and ETH/USD on Kraken. Paper-first, fully auditable, no black boxes.

## Overview
Driftwood is an automated crypto trading system that follows trends using clear, rule-based logic across multiple timeframes.

It fetches market data, computes technical signals, checks strict risk controls, simulates trades (in paper mode), and logs every decision so you can audit exactly what happened and why.

Core philosophy:
- Deterministic: same inputs produce the same outputs.
- Auditable: every action is stored and reviewable.
- Paper-first: test safely before real capital.
- Risk-gated: no trade is allowed without passing risk rules.

What it is **not**:
- Not an AI-driven trading loop.
- Not a black-box strategy.
- Not blind automation without controls.

## Features
- Multi-timeframe trend following (1h, 4h, 1d)
- Risk engine with hard limits and kill switch
- Paper trading mode before real capital
- SQLite database with full audit trail
- Live Streamlit dashboard
- Automated hourly scheduler

## Architecture
- `config/`: strategy/risk configuration and config loader
- `data/`: Kraken market data client and SQLite database layer
- `signals/`: indicators and multi-timeframe signal logic
- `risk/`: position sizing, risk gates, halt logic
- `execution/`: paper trade execution and event logging
- `dashboard/`: Streamlit web dashboard
- `scheduler.py`: main entry point that runs the automated loop on schedule

## Tech Stack
| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Exchange API | ccxt |
| Storage | SQLite + SQLAlchemy |
| Dashboard | Streamlit |
| Charts | Plotly |
| Scheduling | APScheduler |
| Data Processing | pandas |
| Environment Management | python-dotenv |

## Prerequisites
- Mac with Python 3.12+
- A Kraken account (free, no API keys needed for paper trading)
- Git

## Installation
1. Clone the repository:
```bash
git clone https://github.com/FabrizioMarras/driftwood.git
cd driftwood
```

2. Create a virtual environment:
```bash
python3 -m venv .venv
```

3. Activate the virtual environment:
```bash
source .venv/bin/activate
```

4. Install dependencies:
```bash
pip install -r requirements.txt
```

5. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

6. Review `config/config.yaml` and adjust settings if needed.

## Configuration
Key settings in `config/config.yaml`:
- `general.mode`: `paper` or `live`
- `trading.pairs`: which pairs the system trades
- `trading.timeframes`: three timeframe roles used by the strategy
- `risk`: risk limits (position size, daily loss, drawdown, max open trades, etc.)
- `portfolio.initial_capital_usd`: starting portfolio value for simulation/risk calculations

## How to Run
Every time you want to start Driftwood:

1. Open a terminal in the project folder.
2. Check if the virtual environment is active. Your prompt should start with `(.venv)`.
3. If it is not active, run:
```bash
source .venv/bin/activate
```
4. Start Driftwood:
```bash
./start.sh
```
5. Open the dashboard in your browser:
```text
http://localhost:8501
```
6. To stop everything, press `Ctrl+C`.

What `start.sh` does:
- Activates `.venv`
- Starts `scheduler.py` in the background
- Writes scheduler output to `logs/scheduler.log`
- Starts Streamlit dashboard in the foreground

Important:
- The system stops when you close the terminal or laptop.
- Always restart with `./start.sh` when you want Driftwood running again.

To watch scheduler logs live, open a second terminal tab:
```bash
tail -f logs/scheduler.log
```

## How It Works
Trading loop in plain English:
- Every hour: fetch new candles -> compute signals -> check risk gates -> execute or skip -> log everything.
- Three-timeframe confirmation: all three timeframes must align before a trade is taken.
- Risk gates enforce daily loss limit, max drawdown, and max open trades.
- In paper mode, trades are simulated only; no real money moves.

## Going Live (When Ready)
Checklist before switching to live mode:
- Run at least 4 weeks in paper mode with acceptable results.
- Add real Kraken API keys to `.env`.
- Set Kraken API permissions to trade-only (never withdrawal).
- Change `general.mode` to `live` in `config/config.yaml`.
- Start with 25% of your intended capital.

Warning: only do this when you fully understand every trade the system has made.

## Dashboard Guide
Sidebar:
- 🌊 Driftwood logo and mode badge (green = paper, red = live)
- Last cycle time — shows how long ago the system last ran (e.g. "3 mins ago")
- Refresh button, Pause trading toggle, Kill switch
- Current UTC time

Section 1 — System Status:
Four metrics side by side: system status (Running/HALTED), trading mode, open trades count, last cycle time.

Section 2 — Signal Panel:
One card per pair (BTC/USD, ETH/USD) showing: large colored signal box (green=BUY, red=SELL, grey=HOLD), current price, trend direction per timeframe with color coding, momentum per timeframe with color coding, volume confirmation, and reason text.

Section 3 — Risk Utilization:
Daily loss % used with a progress bar (turns red above 50% of limit), open trades vs maximum allowed, portfolio value with change from starting capital.

Section 4 — Equity Curve:
Filled line chart showing portfolio value over time based on cumulative closed trade PnL. Appears once the first trade is closed.

Section 5 — Price Chart:
Candlestick chart with fast and slow moving average overlays. Pair selector (BTC/USD or ETH/USD) and timeframe selector (1h, 4h, 1d).

Section 6 — Trade History:
Summary metrics (total realized PnL, win rate, total trades) followed by full trade table with PnL colored green/red.

Section 7 — Recent Decisions:
Last 15 system events with human readable icons: ✅ Trade Opened, 🔒 Trade Closed, ⏭ Skipped, ❌ Error, ℹ️ Info.

## Disclaimer
- This is a personal project for educational and paper-trading purposes.
- Past performance does not guarantee future results.
- Never risk money you cannot afford to lose.
- The author is not a financial advisor.
