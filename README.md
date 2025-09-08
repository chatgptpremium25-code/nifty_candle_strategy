# NIFTY 14:40 Breakout Bot (Upstox)

Requirements:
- Python 3.10+
- Upstox API access enabled (orders + marketdata)

## Setup
1. Copy `.env.example` to `.env` and set your values. Keep API secret private.
2. (If possible) create a venv and install deps:
   - `python3 -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt`

If you cannot use venv here, install with `pip3 install --break-system-packages -r requirements.txt` (not recommended system-wide).

## First-time auth
```bash
python -m bot.runner --auth
```
- Open the printed URL, log in, copy the `code` from the redirect URI, and paste back.
- This stores `tokens.json` for future runs.

## Run the bot
```bash
python -m bot.runner
```
- The bot:
  - Computes the 14:40 candle high/low on NIFTY index
  - Waits for a breakout and enters CE above / PE below
  - Picks weekly expiry ATM strike rounded to nearest 50
  - Places MARKET orders with product from `.env` (default MIS)
  - Enforces rupee stop loss (default ₹5,000)
  - Squares off at ~15:25 if SL not hit

## Notes
- This is an example; verify instrument formats, holidays, and API permissions.
- Paper trade first. Real trading is at your own risk.