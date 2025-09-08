from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pytz

from .instruments import InstrumentResolver, IST
from .strategy import BreakoutLevels
from .upstox_client import UpstoxClient


@dataclass
class TradeResult:
	trade_date: dt.date
	direction: str  # CE or PE
	entry_time: dt.datetime
	entry_price: float
	exit_time: dt.datetime
	exit_price: float
	quantity: int
	pnl_rupees: float
	reason: str  # SL or EOD


class Backtester:
	def __init__(self, client: UpstoxClient, resolver: InstrumentResolver, stop_loss_rupees: float) -> None:
		self.client = client
		self.resolver = resolver
		self.stop_loss_rupees = stop_loss_rupees

	def _parse_dt(self, ts: Any) -> Optional[dt.datetime]:
		try:
			c_dt = dt.datetime.fromisoformat(str(ts))
			if c_dt.tzinfo is None:
				c_dt = c_dt.replace(tzinfo=IST)
			return c_dt
		except Exception:
			return None

	def _candles_map(self, candles: List[List[Any]]) -> Dict[dt.datetime, List[Any]]:
		m: Dict[dt.datetime, List[Any]] = {}
		for c in candles:
			c_dt = self._parse_dt(c[0])
			if not c_dt:
				continue
			m[c_dt] = c
		return m

	def _find_reference_levels(self, nifty_key: str, day: dt.date) -> Optional[BreakoutLevels]:
		start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
		end = dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST)
		candles = self.client.get_historical_candles(nifty_key, "5minute", start, end)
		target_dt = dt.datetime.combine(day, dt.time(14, 40), tzinfo=IST)
		for c in candles:
			c_dt = self._parse_dt(c[0])
			if c_dt == target_dt:
				return BreakoutLevels(ref_time=target_dt, high=float(c[2]), low=float(c[3]))
		return None

	def _first_breakout(self, nifty_map: Dict[dt.datetime, List[Any]], levels: BreakoutLevels) -> Optional[Tuple[str, dt.datetime]]:
		# Start 14:45 up to 15:10
		start_dt = dt.datetime.combine(levels.ref_time.date(), dt.time(14, 45), tzinfo=IST)
		end_dt = dt.datetime.combine(levels.ref_time.date(), dt.time(15, 10), tzinfo=IST)
		t = start_dt
		while t <= end_dt:
			c = nifty_map.get(t)
			if c:
				high = float(c[2])
				low = float(c[3])
				if high > levels.high:
					return ("CE", t)
				if low < levels.low:
					return ("PE", t)
			t += dt.timedelta(minutes=5)
		return None

	def _option_entry_exit(self, direction: str, entry_time: dt.datetime, underlying_ltp_at_entry: float, day: dt.date) -> Optional[TradeResult]:
		ce_key, pe_key = self.resolver.find_atm_option_keys(underlying_ltp_at_entry, now_ist=entry_time)
		opt_key = ce_key if direction == "CE" else pe_key
		if not opt_key:
			return None
		lot = self.resolver.get_lot_size(opt_key)
		start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
		end = dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST)
		candles = self.client.get_historical_candles(opt_key, "5minute", start, end)
		omap = self._candles_map(candles)
		entry_c = omap.get(entry_time)
		if not entry_c:
			# if exact timestamp missing, try next bar
			entry_c = omap.get(entry_time + dt.timedelta(minutes=5))
			entry_time = entry_time + dt.timedelta(minutes=5) if entry_c else entry_time
		if not entry_c:
			return None
		entry_price = float(entry_c[1])  # open of the bar
		# iterate forward for SL or EOD ~15:25
		deadline = dt.datetime.combine(day, dt.time(15, 25), tzinfo=IST)
		t = entry_time
		opt_mult = 1.0
		qty = lot if lot > 0 else 1
		while t <= deadline:
			c = omap.get(t)
			if c:
				high = float(c[2])
				low = float(c[3])
				# For long option, SL if bar low drops enough below entry
				pnl_low = (low - entry_price) * qty
				if pnl_low <= -abs(self.stop_loss_rupees):
					return TradeResult(
						trade_date=day,
						direction=direction,
						entry_time=entry_time,
						entry_price=entry_price,
						exit_time=t,
						exit_price=low,
						quantity=qty,
						pnl_rupees=(low - entry_price) * qty,
						reason="SL",
					)
			t += dt.timedelta(minutes=5)
		# EOD exit at 15:25 bar close (or last available close <= deadline)
		last_bar = omap.get(deadline) or omap.get(deadline - dt.timedelta(minutes=5)) or entry_c
		exit_price = float(last_bar[4])
		return TradeResult(
			trade_date=day,
			direction=direction,
			entry_time=entry_time,
			entry_price=entry_price,
			exit_time=deadline,
			exit_price=exit_price,
			quantity=qty,
			pnl_rupees=(exit_price - entry_price) * qty,
			reason="EOD",
		)

	def run(self, start_date: dt.date, end_date: dt.date) -> List[TradeResult]:
		results: List[TradeResult] = []
		nifty_key = self.resolver.find_nifty_index_key()
		if not nifty_key:
			raise RuntimeError("Could not resolve NIFTY index instrument key")
		day = start_date
		while day <= end_date:
			levels = self._find_reference_levels(nifty_key, day)
			if not levels:
				day += dt.timedelta(days=1)
				continue
			# Map underlying candles
			u_candles = self.client.get_historical_candles(nifty_key, "5minute", dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST), dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST))
			nmap = self._candles_map(u_candles)
			br = self._first_breakout(nmap, levels)
			if not br:
				day += dt.timedelta(days=1)
				continue
			dirn, entry_time = br
			entry_under_c = nmap.get(entry_time)
			under_entry_price = float(entry_under_c[4]) if entry_under_c else levels.high if dirn == "CE" else levels.low
			tr = self._option_entry_exit(dirn, entry_time, under_entry_price, day)
			if tr:
				results.append(tr)
			day += dt.timedelta(days=1)
		return results

	@staticmethod
	def summarize(results: List[TradeResult]) -> Dict[str, Any]:
		total = sum(r.pnl_rupees for r in results)
		wins = sum(1 for r in results if r.pnl_rupees > 0)
		losses = sum(1 for r in results if r.pnl_rupees <= 0)
		avg = total / len(results) if results else 0.0
		return {
			"trades": len(results),
			"wins": wins,
			"losses": losses,
			"net_pnl": total,
			"avg_pnl": avg,
		}