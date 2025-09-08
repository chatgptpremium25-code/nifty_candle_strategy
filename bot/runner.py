from __future__ import annotations

import argparse
import datetime as dt
import time

import pytz

from .auth import UpstoxAuth
from .config import get_settings
from .upstox_client import UpstoxClient
from .instruments import InstrumentResolver, IST
from .strategy import BreakoutStrategy


def do_auth_flow() -> None:
	auth = UpstoxAuth()
	url = auth.get_login_url()
	print("Open this URL, log in, and capture the 'code' from redirect:")
	print(url)
	code = input("Paste code here: ").strip()
	bundle = auth.exchange_code_for_token(code)
	print("Access token saved. Expires in:", bundle.expires_in)


def run_daily(trade_today: bool = True, enforce_sl: bool = True) -> None:
	settings = get_settings()
	auth = UpstoxAuth()
	client = UpstoxClient(auth)
	resolver = InstrumentResolver(client)
	strategy = BreakoutStrategy(
		client=client,
		resolver=resolver,
		account_balance=settings.account_balance,
		stop_loss_rupees=settings.stop_loss_rupees,
		product=settings.order_product,
		variety=settings.order_variety,
	)

	now = dt.datetime.now(tz=IST)
	trade_date = now.date()
	nifty_key = resolver.find_nifty_index_key()
	if not nifty_key:
		raise RuntimeError("Could not resolve NIFTY index instrument key")
	levels = strategy.get_reference_candle(nifty_key, trade_date)
	if not levels:
		raise RuntimeError("Could not find 14:40 candle for today")
	print(f"Reference 14:40 levels: High={levels.high} Low={levels.low}")

	if trade_today:
		print("Waiting for breakout...")
		side = strategy.wait_for_breakout_and_trade(levels)
		print("Entry side:", side)
		if enforce_sl and side is not None:
			print("Enforcing rupee stop loss...")
			deadline = dt.datetime.combine(trade_date, dt.time(15, 25), tzinfo=IST)
			while dt.datetime.now(tz=IST) < deadline:
				if strategy.enforce_rupee_stop_loss():
					print("Stop loss hit, exited.")
					break
				time.sleep(1)
			print("Square-off time reached or SL hit.")


def main() -> None:
	parser = argparse.ArgumentParser(description="NIFTY 14:40 breakout bot")
	parser.add_argument("--auth", action="store_true", help="Run first-time auth flow")
	parser.add_argument("--no-trade", action="store_true", help="Do not place orders, only compute levels")
	args = parser.parse_args()

	if args.auth:
		do_auth_flow()
		return

	run_daily(trade_today=not args.no_trade, enforce_sl=True)


if __name__ == "__main__":
	main()