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
from .backtest import Backtester


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


def run_backtest(months: int | None, from_date: str | None, to_date: str | None) -> None:
	auth = UpstoxAuth()
	client = UpstoxClient(auth)
	resolver = InstrumentResolver(client)
	settings = get_settings()
	bt = Backtester(client, resolver, settings.stop_loss_rupees)
	if months:
		end = dt.datetime.now(tz=IST).date()
		start = end - dt.timedelta(days=months * 30)
	else:
		if not from_date or not to_date:
			raise ValueError("Provide --months N or both --from YYYY-MM-DD and --to YYYY-MM-DD")
		start = dt.date.fromisoformat(from_date)
		end = dt.date.fromisoformat(to_date)
	results = bt.run(start, end)
	summary = Backtester.summarize(results)
	print("Summary:", summary)
	for r in results:
		print(
			f"{r.trade_date} {r.direction} entry={r.entry_time.time()} {r.entry_price:.2f} "
			f"exit={r.exit_time.time()} {r.exit_price:.2f} qty={r.quantity} pnl={r.pnl_rupees:.2f} {r.reason}"
		)


def main() -> None:
	parser = argparse.ArgumentParser(description="NIFTY 14:40 breakout bot")
	parser.add_argument("--auth", action="store_true", help="Run first-time auth flow")
	parser.add_argument("--no-trade", action="store_true", help="Do not place orders, only compute levels")
	parser.add_argument("--backtest", action="store_true", help="Run backtest instead of live mode")
	parser.add_argument("--months", type=int, default=None, help="Backtest last N months")
	parser.add_argument("--from", dest="from_date", default=None, help="Backtest start date YYYY-MM-DD")
	parser.add_argument("--to", dest="to_date", default=None, help="Backtest end date YYYY-MM-DD")
	args = parser.parse_args()

	if args.auth:
		do_auth_flow()
		return

	if args.backtest:
		run_backtest(args.months, args.from_date, args.to_date)
		return

	run_daily(trade_today=not args.no_trade, enforce_sl=True)


if __name__ == "__main__":
	main()