# Trading Agent

An AI-powered options trading signal agent that uses Claude to analyze market conditions and recommend options strategies, with automated Gmail alerts.

## Architecture

```
trading-agent/
├── data/           # Market data fetching via yfinance
├── strategy/       # Options strategy builders
│   ├── iron_condor.py
│   ├── bull_call_spread.py
│   └── strangle.py
├── agent/          # Claude API integration for signal generation
├── alerts/         # Gmail email notifications
├── config/         # Settings loaded from .env
├── logs/           # Trade signal logs
└── main.py         # Entry point with scheduler
```

## Strategies

| Strategy | When Used | Risk |
|---|---|---|
| Iron Condor | Range-bound market, moderate IV | Medium |
| Bull Call Spread | Moderate bullish outlook | Low |
| Strangle | Large move expected (earnings, macro) | High |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env` and fill in your keys:

```bash
ANTHROPIC_KEY=         # Anthropic API key
SUPABASE_URL=          # Supabase project URL
SUPABASE_KEY=          # Supabase anon key
GMAIL_SENDER=          # Gmail address used to send alerts
GMAIL_APP_PASSWORD=    # Gmail App Password (not your main password)
ALERT_RECIPIENT=       # Email to receive alerts
TRADING_SYMBOL=SPY     # Ticker to analyze
```

> For Gmail, create an App Password at https://myaccount.google.com/apppasswords

### 3. Run the agent

```bash
python main.py
```

The agent runs an immediate analysis on startup, then re-runs every 30 minutes.

## How It Works

1. `MarketData` fetches live price, historical data, and options chain via yfinance
2. `TradingAgent` sends market context to Claude and receives a structured JSON signal
3. The appropriate strategy module builds the trade legs based on the signal
4. `AlertManager` emails the signal and trade details to the configured recipient
5. All signals are logged to `logs/trade_signals.log`

## Extending

- Add new strategies in `strategy/` and register them in `main.py`'s `strategy_map`
- Store signals to Supabase by adding a `supabase` client call after signal generation
- Add a position tracker to feed open positions back to `agent.evaluate_position()`
