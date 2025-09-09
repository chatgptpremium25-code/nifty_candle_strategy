from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import requests

from .auth import UpstoxAuth, UPSTOX_BASE_URL


class UpstoxClient:
	def __init__(self, auth: UpstoxAuth) -> None:
		self.auth = auth
		self.session = auth.get_authorized_session()

	def get_instruments_master(self) -> List[Dict[str, Any]]:
		url = f"{UPSTOX_BASE_URL}/v2/instruments"
		r = self.session.get(url, timeout=60)
		if r.status_code == 404:
			url_fallback = f"{UPSTOX_BASE_URL}/v2/market/instruments"
			r = self.session.get(url_fallback, timeout=60)
		r.raise_for_status()
		data = r.json().get("data") or r.json()
		if isinstance(data, dict) and "instruments" in data:
			data = data["instruments"]
		return data if isinstance(data, list) else []

	def get_option_chain(self, underlying_instrument_key: str, expiry_date: Optional[str] = None) -> List[Dict[str, Any]]:
		url = f"{UPSTOX_BASE_URL}/v2/option/chain"
		params: Dict[str, Any] = {"instrument_key": underlying_instrument_key}
		if expiry_date:
			params["expiry_date"] = expiry_date
		r = self.session.get(url, params=params, timeout=30)
		r.raise_for_status()
		return r.json().get("data") or []

	def get_historical_candles_v3(self, instrument_key: str, unit: str, interval: int, from_date: dt.date, to_date: dt.date) -> List[List[Any]]:
		# v3 endpoint expects /v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to}/{from}
		url = f"https://api.upstox.com/v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date.isoformat()}/{from_date.isoformat()}"
		r = self.session.get(url, timeout=60)
		r.raise_for_status()
		data = r.json().get("data") or {}
		return data.get("candles") or []

	def get_historical_candles(self, instrument_key: str, interval: str, from_date: dt.datetime, to_date: dt.datetime) -> List[Dict[str, Any]]:
		to_s = to_date.strftime("%Y-%m-%d")
		from_s = from_date.strftime("%Y-%m-%d")
		url = f"{UPSTOX_BASE_URL}/v2/historical-candle/{instrument_key}/{interval}/{to_s}/{from_s}"
		r = self.session.get(url, timeout=60)
		r.raise_for_status()
		payload = r.json().get("data", {})
		candles = payload.get("candles") if isinstance(payload, dict) else []
		return candles or []

	def get_open_chart_candles(self, instrument_key: str, interval_code: str, from_ms: int, limit: int = 500) -> List[List[Any]]:
		url = "https://service.upstox.com/chart/open/v3/candles"
		params = {
			"instrumentKey": instrument_key,
			"interval": interval_code,
			"from": str(from_ms),
			"limit": str(limit),
		}
		r = requests.get(url, params=params, timeout=30)
		r.raise_for_status()
		data = r.json().get("data") or {}
		return data.get("candles") or []

	def get_ltp(self, instrument_key: str) -> float:
		url = f"{UPSTOX_BASE_URL}/v2/market-quote/ltp"
		params = {"instrument_key": instrument_key}
		r = self.session.get(url, params=params, timeout=10)
		r.raise_for_status()
		data = r.json().get("data", {})
		val = data.get(instrument_key, {})
		return float(val.get("last_price"))

	def place_order(self, instrument_key: str, side: str, quantity: int, product: str, variety: str, order_type: str = "MARKET", price: Optional[float] = None) -> Dict[str, Any]:
		url = f"{UPSTOX_BASE_URL}/v2/order/place"
		payload = {
			"instrument_key": instrument_key,
			"quantity": quantity,
			"product": product,
			"order_type": order_type,
			"transaction_type": "BUY" if side.lower() == "buy" else "SELL",
			"validity": "DAY",
			"variety": variety,
		}
		if order_type == "LIMIT" and price is not None:
			payload["price"] = round(float(price), 2)
		r = self.session.post(url, json=payload, timeout=15)
		r.raise_for_status()
		return r.json().get("data") or r.json()

	def get_positions(self) -> List[Dict[str, Any]]:
		url = f"{UPSTOX_BASE_URL}/v2/portfolio/positions"
		r = self.session.get(url, timeout=10)
		r.raise_for_status()
		return r.json().get("data") or []

	def exit_position(self, instrument_key: str, quantity: Optional[int] = None) -> Dict[str, Any]:
		url = f"{UPSTOX_BASE_URL}/v2/order/exit"
		payload = {
			"instrument_key": instrument_key,
		}
		if quantity:
			payload["quantity"] = quantity
		r = self.session.post(url, json=payload, timeout=10)
		r.raise_for_status()
		return r.json().get("data") or r.json()