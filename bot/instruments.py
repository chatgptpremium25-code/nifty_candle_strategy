from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from .upstox_client import UpstoxClient

IST = pytz.timezone("Asia/Kolkata")


class InstrumentResolver:
	def __init__(self, client: UpstoxClient, cache_path: str | Path = "instruments_cache.json") -> None:
		self.client = client
		self.cache_path = Path(cache_path)
		self.cache: Optional[List[Dict[str, Any]]] = None

	def _load_cache(self) -> Optional[List[Dict[str, Any]]]:
		if self.cache_path.exists():
			try:
				return json.loads(self.cache_path.read_text())
			except Exception:
				return None
		return None

	def _save_cache(self, data: List[Dict[str, Any]]) -> None:
		self.cache_path.write_text(json.dumps(data) )

	def get_all(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
		if not force_refresh:
			cached = self._load_cache()
			if cached:
				self.cache = cached
				return cached
		data = self.client.get_instruments_master()
		self._save_cache(data)
		self.cache = data
		return data

	def find_nifty_index_key(self) -> Optional[str]:
		# Use known index key provided by user/environment; fallback to standard label
		return os.getenv("NIFTY_INDEX_KEY", "NSE_INDEX|Nifty 50")

	def _current_weekly_expiry(self, now_ist: dt.datetime) -> Optional[str]:
		# Return YYYY-MM-DD for Thursday this week
		weekday = now_ist.weekday()
		delta = (3 - weekday) % 7
		return (now_ist + dt.timedelta(days=delta)).date().isoformat()

	def _round_to_nearest_50(self, price: float) -> int:
		return int(round(price / 50.0) * 50)

	def find_atm_option_keys(self, underlying_ltp: float, now_ist: Optional[dt.datetime] = None) -> Tuple[Optional[str], Optional[str]]:
		if now_ist is None:
			now_ist = dt.datetime.now(tz=IST)
		under_key = self.find_nifty_index_key()
		expiry = self._current_weekly_expiry(now_ist)
		chain = self.client.get_option_chain(under_key, expiry)
		if not chain:
			return None, None
		atm = self._round_to_nearest_50(underlying_ltp)
		best_diff = 10**9
		ce_key = None
		pe_key = None
		for row in chain:
			try:
				strike = int(float(row.get("strike_price")))
			except Exception:
				continue
			diff = abs(strike - atm)
			if diff < best_diff:
				best_diff = diff
				co = row.get("call_options") or {}
				po = row.get("put_options") or {}
				ce_key = co.get("instrument_key")
				pe_key = po.get("instrument_key")
		return ce_key, pe_key

	def get_instrument_by_key(self, key: str) -> Optional[Dict[str, Any]]:
		instruments = self.get_all()
		for inst in instruments:
			if inst.get("instrument_key") == key:
				return inst
		return None

	def get_lot_size(self, key: str) -> int:
		try:
			return int(os.getenv("NIFTY_OPTION_LOT_SIZE", "50"))
		except Exception:
			return 50