from __future__ import annotations

import argparse
import datetime as dt
import time
from typing import Optional

import pytz

from .auth import UpstoxAuth
from .config import get_settings
from .upstox_client import UpstoxClient
from .instruments import InstrumentResolver, IST
from .strategy import BreakoutStrategy
from .backtest import Backtester


def do_auth_flow(auth_code: Optional[str]) -> None:
	auth = UpstoxAuth()
	if not auth_code:
		url = auth.get_login_url()
		print("Open this URL, log in, then rerun with --auth-code=<code>:")
		print(url)
		return
	bundle = auth.exchange_code_for_token(auth_code)
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


def place_order_now(instrument_key: Optional[str], atm_ce: bool, atm_pe: bool, qty: Optional[int]) -> None:
	settings = get_settings()
	auth = UpstoxAuth()
	client = UpstoxClient(auth)
	resolver = InstrumentResolver(client)
	if not instrument_key:
		nifty_key = resolver.find_nifty_index_key()
		ltp = client.get_ltp(nifty_key)
		ce_key, pe_key = resolver.find_atm_option_keys(ltp)
		if atm_ce:
			instrument_key = ce_key
		elif atm_pe:
			instrument_key = pe_key
		if not instrument_key:
			raise RuntimeError("Could not resolve ATM option instrument key")
	if qty is None or qty <= 0:
		qty = resolver.get_lot_size(instrument_key)
	resp = client.place_order(
		instrument_key=instrument_key,
		side="buy",
		quantity=qty,
		product=settings.order_product,
		variety=settings.order_variety,
		order_type="MARKET",
	)
	print("Order placed:", resp)


def run_one_pm_breakout(budget_rupees: float = 600.0, stop_loss_rupees: float = 100.0) -> None:
	auth = UpstoxAuth()
	client = UpstoxClient(auth)
	resolver = InstrumentResolver(client)
	nifty_key = resolver.find_nifty_index_key()
	# Get 13:00 candle high/low
	today = dt.datetime.now(tz=IST).date()
	# Fetch NIFTY 5m candles for this month via v3
	month_start = today.replace(day=1)
	candles = client.get_historical_candles_v3(nifty_key, "minutes", 5, month_start, today)
	# Find 13:00 bar
	ref_high = None
	ref_low = None
	for c in candles:
		# [ts, o, h, l, c, v]
		try:
			c_dt = dt.datetime.fromisoformat(str(c[0]))
			if c_dt.tzinfo is None:
				c_dt = c_dt.replace(tzinfo=IST)
		except Exception:
			# epoch ms
			c_dt = dt.datetime.fromtimestamp(int(c[0]) / 1000.0, tz=IST)
		if c_dt.date() == today and c_dt.hour == 13 and c_dt.minute == 0:
			ref_high = float(c[2])
			ref_low = float(c[3])
			break
	if ref_high is None or ref_low is None:
		raise RuntimeError("Could not find 13:00 candle today")
	print(f"1:00 PM levels: High={ref_high} Low={ref_low}")
	# Wait for breakout
	deadline = dt.datetime.combine(today, dt.time(15, 10), tzinfo=IST)
	entered = False
	pos_key = None
	pos_qty = 0
	entry_price = 0.0
	while dt.datetime.now(tz=IST) < deadline and not entered:
		ltp = client.get_ltp(nifty_key)
		if ltp > ref_high:
			# take CE within budget
			opt_key = resolver.find_affordable_option_key(ltp, side="ce", budget_rupees=budget_rupees)
			if opt_key:
				qty = 1
				client.place_order(opt_key, side="buy", quantity=qty, product="MIS", variety="REGULAR", order_type="MARKET")
				pos_key = opt_key
				pos_qty = qty
				entry_price = client.get_ltp(opt_key)
				entered = True
				print("Entered CE")
		elif ltp < ref_low:
			opt_key = resolver.find_affordable_option_key(ltp, side="pe", budget_rupees=budget_rupees)
			if opt_key:
				qty = 1
				client.place_order(opt_key, side="buy", quantity=qty, product="MIS", variety="REGULAR", order_type="MARKET")
				pos_key = opt_key
				pos_qty = qty
				entry_price = client.get_ltp(opt_key)
				entered = True
				print("Entered PE")
		time.sleep(0.5)
	if not entered:
		print("No breakout until deadline.")
		return
	# Enforce ₹100 SL
	while dt.datetime.now(tz=IST) < dt.datetime.combine(today, dt.time(15, 25), tzinfo=IST):
		ltp = client.get_ltp(pos_key)
		pnl = (ltp - entry_price) * pos_qty
		if pnl <= -abs(stop_loss_rupees):
			client.place_order(pos_key, side="sell", quantity=pos_qty, product="MIS", variety="REGULAR", order_type="MARKET")
			print("Stop loss hit. Exited.")
			return
		time.sleep(1)
	print("Square-off time reached.")


def schedule_onepm_once() -> None:
	now = dt.datetime.now(tz=IST)
	# compute next weekday 13:00 IST
	candidate = dt.datetime.combine(now.date(), dt.time(13, 0), tzinfo=IST)
	if now >= candidate:
		candidate = candidate + dt.timedelta(days=1)
	# skip weekends
	while candidate.weekday() >= 5:
		candidate = candidate + dt.timedelta(days=1)
	print(f"Scheduled 1:00 PM breakout at {candidate}")
	while True:
		now = dt.datetime.now(tz=IST)
		delta = (candidate - now).total_seconds()
		if delta <= 0:
			break
		time.sleep(min(60, max(1, int(delta))))
	try:
		run_one_pm_breakout(budget_rupees=600.0, stop_loss_rupees=100.0)
	except Exception as e:
		print(f"Run failed: {e}")


def place_amo_cheapest_pe(budget_rupees: float = 600.0) -> None:
	auth = UpstoxAuth()
	client = UpstoxClient(auth)
	resolver = InstrumentResolver(client)
	nifty_key = resolver.find_nifty_index_key()
	ltp = client.get_ltp(nifty_key)
	opt_key = resolver.find_affordable_option_key(ltp, side="pe", budget_rupees=budget_rupees)
	if not opt_key:
		raise RuntimeError("No PE found within budget")
	lot = resolver.get_lot_size(opt_key)
	resp = client.place_order(opt_key, side="buy", quantity=lot, product="MIS", variety="REGULAR", order_type="MARKET", is_amo=True)
	print("AMO placed:", resp)


def main() -> None:
	parser = argparse.ArgumentParser(description="NIFTY 14:40 breakout bot")
	parser.add_argument("--auth", action="store_true", help="Run auth flow. If --auth-code omitted, prints login URL")
	parser.add_argument("--auth-code", default=None, help="Authorization code from redirect URL")
	parser.add_argument("--no-trade", action="store_true", help="Do not place orders, only compute levels")
	parser.add_argument("--backtest", action="store_true", help="Run backtest instead of live mode")
	parser.add_argument("--months", type=int, default=None, help="Backtest last N months")
	parser.add_argument("--from", dest="from_date", default=None, help="Backtest start date YYYY-MM-DD")
	parser.add_argument("--to", dest="to_date", default=None, help="Backtest end date YYYY-MM-DD")
	parser.add_argument("--place-order", action="store_true", help="Place an immediate order")
	parser.add_argument("--instrument-key", dest="instrument_key", default=None, help="Instrument key to buy")
	parser.add_argument("--atm-ce", action="store_true", help="Auto-resolve and buy NIFTY ATM CE")
	parser.add_argument("--atm-pe", action="store_true", help="Auto-resolve and buy NIFTY ATM PE")
	parser.add_argument("--qty", type=int, default=None, help="Quantity to buy (defaults to lot size)")
	parser.add_argument("--one-pm-breakout", action="store_true", help="Run 1:00 PM breakout with small budget and SL")
	parser.add_argument("--schedule-onepm", action="store_true", help="Schedule next weekday 13:00 IST one-pm breakout run")
	parser.add_argument("--amo-cheapest-pe", action="store_true", help="Place AMO for cheapest near-ATM PE within budget")
	args = parser.parse_args()

	if args.auth:
		do_auth_flow(args.auth_code)
		return

	if args.backtest:
		run_backtest(args.months, args.from_date, args.to_date)
		return

	if args.place_order:
		place_order_now(args.instrument_key, args.atm_ce, args.atm_pe, args.qty)
		return

	if args.one_pm_breakout:
		run_one_pm_breakout(budget_rupees=600.0, stop_loss_rupees=100.0)
		return

	if args.schedule_onepm:
		schedule_onepm_once()
		return

	if args.amo_cheapest_pe:
		place_amo_cheapest_pe(budget_rupees=600.0)
		return

	run_daily(trade_today=not args.no_trade, enforce_sl=True)


if __name__ == "__main__":
	main()