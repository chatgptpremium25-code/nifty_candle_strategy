from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import time
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
		self._month_cache: Dict[Tuple[str, int, int], List[List[Any]]] = {}

	def _parse_any_ts(self, v: Any) -> dt.datetime:
		if isinstance(v, (int, float)):
			return dt.datetime.fromtimestamp(float(v) / 1000.0, tz=IST)
		try:
			iv = int(v)
			return dt.datetime.fromtimestamp(iv / 1000.0, tz=IST)
		except Exception:
			pass
		d = dt.datetime.fromisoformat(str(v))
		if d.tzinfo is None:
			d = d.replace(tzinfo=IST)
		return d

	def _candles_map(self, candles: List[List[Any]]) -> Dict[dt.datetime, List[Any]]:
		m: Dict[dt.datetime, List[Any]] = {}
		for c in candles:
			c_dt = self._parse_any_ts(c[0])
			m[c_dt] = c
		return m

	def _get_month_bounds(self, d: dt.date) -> Tuple[dt.date, dt.date]:
		start = d.replace(day=1)
		if start.month == 12:
			end = start.replace(year=start.year + 1, month=1, day=1) - dt.timedelta(days=1)
		else:
			end = start.replace(month=start.month + 1, day=1) - dt.timedelta(days=1)
		return start, end

	def _fetch_month_v3_cached(self, instrument_key: str, day: dt.date) -> List[List[Any]]:
		key = (instrument_key, day.year, day.month)
		if key in self._month_cache:
			return self._month_cache[key]
		start, end = self._get_month_bounds(day)
		candles = self.client.get_historical_candles_v3(instrument_key, "minutes", 5, start, end)
		# brief throttle to avoid rate limits
		time.sleep(0.2)
		self._month_cache[key] = candles
		return candles

	def _find_candle_by_hm(self, candles: List[List[Any]], day: dt.date, hour: int, minute: int) -> Optional[List[Any]]:
		target = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=IST)
		best = None
		for c in candles:
			c_dt = self._parse_any_ts(c[0])
			if c_dt.date() == day and c_dt.hour == hour and c_dt.minute == minute:
				best = c
				break
		return best

	def _find_reference_levels(self, nifty_key: str, day: dt.date) -> Optional[BreakoutLevels]:
		candles = self._fetch_month_v3_cached(nifty_key, day)
		c = self._find_candle_by_hm(candles, day, 14, 40)
		if not c:
			return None
		return BreakoutLevels(ref_time=dt.datetime.combine(day, dt.time(14, 40), tzinfo=IST), high=float(c[2]), low=float(c[3]))

	def _first_breakout(self, nifty_map: Dict[dt.datetime, List[Any]], levels: BreakoutLevels) -> Optional[Tuple[str, dt.datetime, float]]:
		start_dt = dt.datetime.combine(levels.ref_time.date(), dt.time(14, 45), tzinfo=IST)
		end_dt = dt.datetime.combine(levels.ref_time.date(), dt.time(15, 10), tzinfo=IST)
		t = start_dt
		while t <= end_dt:
			cand = nifty_map.get(t) or nifty_map.get(t + dt.timedelta(minutes=5)) or nifty_map.get(t - dt.timedelta(minutes=5))
			if cand:
				high = float(cand[2])
				low = float(cand[3])
				close = float(cand[4])
				if high > levels.high:
					return ("CE", t, close)
				if low < levels.low:
					return ("PE", t, close)
			t += dt.timedelta(minutes=5)
		return None

	def _option_entry_exit(self, direction: str, entry_time: dt.datetime, underlying_ltp_at_entry: float, day: dt.date) -> Optional[TradeResult]:
		ce_key, pe_key = self.resolver.find_atm_option_keys(underlying_ltp_at_entry, now_ist=entry_time)
		opt_key = ce_key if direction == "CE" else pe_key
		if not opt_key:
			return None
		qty = self.resolver.get_lot_size(opt_key)
		candles = self._fetch_month_v3_cached(opt_key, day)
		omap = self._candles_map(candles)
		entry_c = omap.get(entry_time) or omap.get(entry_time + dt.timedelta(minutes=5)) or omap.get(entry_time - dt.timedelta(minutes=5))
		if not entry_c:
			return None
		entry_price = float(entry_c[1])
		deadline = dt.datetime.combine(day, dt.time(15, 25), tzinfo=IST)
		t = entry_time
		while t <= deadline:
			c = omap.get(t) or omap.get(t + dt.timedelta(minutes=5))
			if c:
				low = float(c[3])
				if (low - entry_price) * qty <= -abs(self.stop_loss_rupees):
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
		day = start_date
		while day <= end_date:
			candles = self._fetch_month_v3_cached(nifty_key, day)
			nmap = self._candles_map(candles)
			levels = self._find_reference_levels(nifty_key, day)
			if not levels:
				day += dt.timedelta(days=1)
				continue
			br = self._first_breakout(nmap, levels)
			if not br:
				day += dt.timedelta(days=1)
				continue
			dirn, entry_time, under_close = br
			tr = self._option_entry_exit(dirn, entry_time, under_close, day)
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