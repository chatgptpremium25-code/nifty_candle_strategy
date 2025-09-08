from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Optional

import pytz

from .upstox_client import UpstoxClient
from .instruments import InstrumentResolver, IST


@dataclass
class BreakoutLevels:
	ref_time: dt.datetime
	high: float
	low: float


class BreakoutStrategy:
	def __init__(self, client: UpstoxClient, resolver: InstrumentResolver, account_balance: float, stop_loss_rupees: float, product: str, variety: str) -> None:
		self.client = client
		self.resolver = resolver
		self.account_balance = account_balance
		self.stop_loss_rupees = stop_loss_rupees
		self.product = product
		self.variety = variety
		self.position_opened = False
		self.position_instrument_key: Optional[str] = None
		self.entry_price: Optional[float] = None
		self.side: Optional[str] = None
		self.lot_size: int = 1

	def get_reference_candle(self, nifty_index_key: str, day: dt.date) -> Optional[BreakoutLevels]:
		# Get 5-min candles for the day and extract 14:40 candle
		start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
		end = dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST)
		candles = self.client.get_historical_candles(nifty_index_key, "5minute", start, end)
		# Upstox returns [time, open, high, low, close, volume]
		target_dt = dt.datetime.combine(day, dt.time(14, 40), tzinfo=IST)
		h = None
		l = None
		for c in candles:
			# c[0] may be ISO string
			ts = c[0]
			try:
				c_dt = dt.datetime.fromisoformat(ts)
			except Exception:
				continue
			if c_dt.tzinfo is None:
				c_dt = c_dt.replace(tzinfo=IST)
			if c_dt == target_dt:
				h = float(c[2])
				l = float(c[3])
				break
		if h is None or l is None:
			return None
		return BreakoutLevels(ref_time=target_dt, high=h, low=l)

	def wait_for_breakout_and_trade(self, levels: BreakoutLevels) -> Optional[str]:
		# Only start checking from 14:45 IST
		start_check = dt.datetime.combine(levels.ref_time.date(), dt.time(14, 45), tzinfo=IST)
		while dt.datetime.now(tz=IST) < start_check:
			time.sleep(0.5)
		# Wait until ~15:10 latest
		deadline = dt.datetime.combine(levels.ref_time.date(), dt.time(15, 10), tzinfo=IST)
		while dt.datetime.now(tz=IST) < deadline and not self.position_opened:
			try:
				# Underlying LTP
				nifty_key = self.resolver.find_nifty_index_key()
				if not nifty_key:
					time.sleep(1)
					continue
				ltp = self.client.get_ltp(nifty_key)
				if ltp > levels.high:
					self._enter_option(side="buy_ce", underlying_ltp=ltp)
					return "CE"
				elif ltp < levels.low:
					self._enter_option(side="buy_pe", underlying_ltp=ltp)
					return "PE"
			except Exception:
				pass
			time.sleep(0.25)
		return None

	def _enter_option(self, side: str, underlying_ltp: float) -> None:
		ce_key, pe_key = self.resolver.find_atm_option_keys(underlying_ltp)
		if side == "buy_ce" and ce_key:
			key = ce_key
		elif side == "buy_pe" and pe_key:
			key = pe_key
		else:
			return
		# lot sizing by instrument lot size; 1 lot
		lot = self.resolver.get_lot_size(key)
		quantity = lot if lot > 0 else 1
		order = self.client.place_order(
			instrument_key=key,
			side="buy",
			quantity=quantity,
			product=self.product,
			variety=self.variety,
			order_type="MARKET",
		)
		self.position_opened = True
		self.position_instrument_key = key
		self.entry_price = self.client.get_ltp(key)
		self.side = side
		self.lot_size = quantity

	def enforce_rupee_stop_loss(self) -> bool:
		# Poll PnL and exit if -stop_loss_rupees reached. Returns True if exited.
		if not self.position_opened or not self.position_instrument_key or self.entry_price is None:
			return False
		current = self.client.get_ltp(self.position_instrument_key)
		pnl_per_unit = (current - self.entry_price) if self.side in ("buy_ce", "buy_pe") else (self.entry_price - current)
		pnl_rupees = pnl_per_unit * float(self.lot_size)
		if pnl_rupees <= -abs(self.stop_loss_rupees):
			self.client.place_order(
				instrument_key=self.position_instrument_key,
				side="sell",
				quantity=self.lot_size,
				product=self.product,
				variety=self.variety,
				order_type="MARKET",
			)
			return True
		return False