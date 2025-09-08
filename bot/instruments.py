from __future__ import annotations

import datetime as dt
import json
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
		instruments = self.get_all()
		for inst in instruments:
			if inst.get("exchange") in ("NSE_INDEX", "NSE") and str(inst.get("tradingsymbol", "")).upper() in ("NIFTY 50", "NIFTY50", "NIFTY_50"):
				return inst.get("instrument_key")
		return None

	def _current_weekly_expiry(self, now_ist: dt.datetime) -> dt.date:
		# NIFTY weekly expiry is Thursday (or previous working day for holidays). We assume Thursday here; production should adjust for holidays.
		weekday = now_ist.weekday()  # Monday=0 ... Sunday=6
		delta = (3 - weekday) % 7  # Thursday index 3
		expiry_date = (now_ist + dt.timedelta(days=delta)).date()
		return expiry_date

	def _round_to_nearest_50(self, price: float) -> int:
		return int(round(price / 50.0) * 50)

	def find_atm_option_keys(self, underlying_ltp: float, now_ist: Optional[dt.datetime] = None) -> Tuple[Optional[str], Optional[str]]:
		if now_ist is None:
			now_ist = dt.datetime.now(tz=IST)
		expiry = self._current_weekly_expiry(now_ist)
		strike = self._round_to_nearest_50(underlying_ltp)
		instruments = self.get_all()
		ce_key = None
		pe_key = None
		for inst in instruments:
			if inst.get("segment") == "NFO-OPT" and str(inst.get("name", "")).upper().startswith("NIFTY"):
				inst_exp = inst.get("expiry")  # e.g., 2025-09-25
				try:
					inst_exp_date = dt.datetime.strptime(inst_exp, "%Y-%m-%d").date()
				except Exception:
					continue
				if inst_exp_date != expiry:
					continue
				if int(float(inst.get("strike_price", 0))) != strike:
					continue
				opt_type = str(inst.get("option_type", "")).upper()
				if opt_type == "CE":
					ce_key = inst.get("instrument_key")
				elif opt_type == "PE":
					pe_key = inst.get("instrument_key")
		return ce_key, pe_key