# Crypto Trading Bot

A Telegram-controlled cryptocurrency trading bot for the Bybit exchange. Supports paper trading and live trading with automated strategy execution, risk management, backtesting, and performance analytics.

## Features

- **3 Trading Strategies** — Trend Following (EMA + MACD), Mean Reversion (RSI + Bollinger Bands), Breakout (ATR + Volume)
- **Market Regime Detection** — ADX-based filter automatically disables strategies in unsuitable conditions
- **Risk Management** — Fixed fractional position sizing, daily loss limits, consecutive-loss cooldown
- **Paper Trading** — Full simulation with virtual balance, fee/slippage modeling, SL/TP tracking
- **Live Trading** — Real order execution on Bybit spot with SL/TP monitoring
- **Telegram Control** — 20 commands for full bot management from your phone
- **Backtesting** — Walk-forward backtester with fee simulation, callable via `/backtest`
- **Performance Analytics** — Win rate, profit factor, Sharpe ratio, drawdown tracking
- **SQLite Persistence** — Trades, signals, settings survive restarts

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Justfriends-hub/moneyminingbot.git
cd moneyminingbot/crypto_bot
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
copy .env.example .env    # Windows
cp .env.example .env      # Linux/macOS
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | **Yes** | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ALLOWED_USER_IDS` | **Yes** | Your numeric Telegram ID (from [@userinfobot](https://t.me/userinfobot)) |
| `BYBIT_API_KEY` | Yes | Testnet: [testnet.bybit.com](https://testnet.bybit.com) / Live: [bybit.com](https://www.bybit.com) |
| `BYBIT_API_SECRET` | Yes | Corresponding API secret |
| `TRADING_MODE` | No | `paper` (default) or `live` |

See [.env.example](crypto_bot/.env.example) for all options.

### 3. Run

```bash
python main.py
```

Then open Telegram and send `/start` to your bot.

## Docker Deployment

```bash
cd moneyminingbot
docker compose up -d
```

Database and logs persist in a Docker volume across restarts.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Check bot is alive |
| `/help` | Show all commands |
| `/status` | View bot state and current signals |
| `/balance` | Check paper/live balance |
| `/positions` | View open trades |
| `/trade_on` | Enable auto-trading |
| `/trade_off` | Disable auto-trading |
| `/setstrategy auto` | Set strategy (auto/trend/mean_reversion/breakout) |
| `/setpair AUTO` | Set pairs (AUTO or BTC/USDT,ETH/USDT,...) |
| `/settimeframe 1h` | Set timeframe (15m/1h/4h) |
| `/setrisk 0.25` | Set risk per trade (%) |
| `/setbalance 500` | Reset paper balance |
| `/performance` | View performance stats |
| `/lasttrades` | View recent trades |
| `/backtest BTC/USDT 1h trend` | Run a backtest |
| `/closeall` | Emergency close all positions |
| `/dailysummary` | View today's summary |

## Architecture

```
crypto_bot/
├── main.py                  # Entry point, scheduler, lifecycle
├── config.py                # Central configuration from .env
├── exchange/
│   ├── bybit_client.py      # ccxt wrapper for Bybit API
│   └── paper_engine.py      # Paper trading simulation
├── strategies/
│   ├── base_strategy.py     # Abstract base + Signal dataclass
│   ├── trend_following.py   # EMA + MACD strategy
│   ├── mean_reversion.py    # RSI + Bollinger Bands strategy
│   ├── breakout.py          # ATR + Volume breakout strategy
│   └── regime_filter.py     # Market condition detection
├── execution/
│   └── trade_executor.py    # Signal scanning, ranking, execution
├── risk/
│   └── risk_manager.py      # Position sizing, trade gating
├── telegram_bot/
│   ├── bot_handler.py       # Telegram command handlers
│   └── notifier.py          # Push notifications
├── database/
│   └── db_manager.py        # SQLite persistence
├── analytics/
│   └── performance.py       # Win rate, drawdown, Sharpe
├── backtesting/
│   └── backtester.py        # Walk-forward backtester
└── utils/
    └── helpers.py           # Shared utilities
```

## Safety Design

- Auto-trading always starts **OFF** — must be enabled with `/trade_on`
- Defaults to **testnet + paper mode** (no real money at risk)
- Paper balance capped at $5–$1,000
- Risk per trade clamped to 0.1%–2%
- Daily loss limit pauses all trading
- Cooldown after consecutive losses
- Telegram commands restricted to authorized user IDs

## Disclaimer

- This bot does **NOT** guarantee profits
- Past backtest results do **NOT** predict future performance
- Crypto markets are highly volatile and unpredictable
- Always start with paper trading before using real money
- Never trade with money you cannot afford to lose
