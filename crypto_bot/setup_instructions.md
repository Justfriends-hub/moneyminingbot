# Crypto Trading Bot - Windows Setup Guide

## Prerequisites
- Windows 10 or 11
- Python 3.10 or higher: https://www.python.org/downloads/
- A Telegram account
- A Bybit account (testnet is free for paper trading)

---

## Step 1: Install Python

1. Download from https://www.python.org/downloads/
2. During install CHECK "Add Python to PATH"
3. Verify: open Command Prompt and run:
   python --version
   (should show 3.10 or higher)

---

## Step 2: Place Bot Files

Put all files in a folder e.g. C:\Users\YourName\crypto_bot\

---

## Step 3: Create Virtual Environment

Open Command Prompt:

   cd C:\Users\YourName\crypto_bot
   python -m venv venv
   venv\Scripts\activate

You will see (venv) at the start of the prompt.

---

## Step 4: Install Dependencies

   pip install -r requirements.txt

Takes 1-3 minutes.

---

## Step 5: Create Your .env File

   copy .env.example .env
   notepad .env

Fill in all the values shown below.

---

## Step 6: Get Telegram Bot Token

1. Open Telegram, search @BotFather
2. Send /newbot
3. Follow prompts - choose a name and username
4. BotFather gives you a token like: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
5. Paste into .env as TELEGRAM_BOT_TOKEN

Get Your Telegram User ID:
1. Search @userinfobot in Telegram
2. Send /start
3. It replies with your numeric ID e.g. 987654321
4. Add to .env as TELEGRAM_ALLOWED_USER_IDS=987654321

---

## Step 7: Get Bybit Testnet API Keys

1. Go to https://testnet.bybit.com - create free account
2. Account > API Management > Create API Key
3. Enable Read and Trade permissions
4. Copy Key and Secret into .env

Your .env for paper trading should look like:

   BYBIT_API_KEY=your_testnet_api_key
   BYBIT_API_SECRET=your_testnet_api_secret
   BYBIT_TESTNET=true
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
   TELEGRAM_ALLOWED_USER_IDS=987654321
   TRADING_MODE=paper
   PAPER_BALANCE=500
   RISK_PER_TRADE=0.25
   MAX_DAILY_LOSS_PCT=2.0
   MAX_CONCURRENT_TRADES=3
   DEFAULT_PAIRS=AUTO
   DEFAULT_TIMEFRAME=1h
   DEFAULT_STRATEGY=AUTO

---

## Step 8: Run the Bot

   cd C:\Users\YourName\crypto_bot
   venv\Scripts\activate
   python main.py

You will see log output. Open Telegram and send /start to your bot.

---

## Step 9: First Commands

   /start             - Check bot is alive
   /balance           - See paper balance
   /setstrategy auto  - Enable all strategies
   /setpair AUTO      - Bot picks best pairs
   /settimeframe 1h   - Set 1h timeframe
   /trade_on          - Enable auto-trading
   /status            - Confirm everything is active
   /performance       - See results after trades
   /backtest BTC/USDT 1h trend  - Run a backtest

---

## Step 10: Enable Live Trading (Read Carefully)

WARNING: Live trading uses real money. You can lose funds.

Only do this after:
- Running paper trading for at least 2-4 weeks
- Reviewing /performance results
- Accepting that past results do not predict future profits

Steps:
1. Create LIVE Bybit API keys at https://www.bybit.com/app/user/api-management
2. Update .env:
   BYBIT_API_KEY=your_live_key
   BYBIT_API_SECRET=your_live_secret
   BYBIT_TESTNET=false
   TRADING_MODE=live
3. Keep RISK_PER_TRADE=0.25 (very conservative)
4. Restart: python main.py
5. Use /trade_on to enable

---

## Troubleshooting

ModuleNotFoundError:
  Make sure venv is active then run: pip install -r requirements.txt

TELEGRAM_BOT_TOKEN is not set:
  Check your .env file exists and token is filled in with no spaces around =

Bot not responding:
  Check bot is running in terminal
  Check your User ID is in TELEGRAM_ALLOWED_USER_IDS
  Send /start again

Could not load markets:
  Bybit API temporarily down - bot will retry automatically

---

## Disclaimers

- This bot does NOT guarantee profits
- Past backtest results do NOT predict future performance  
- Crypto markets are highly volatile and unpredictable
- Always start with paper trading before using real money
- Never trade with money you cannot afford to lose
