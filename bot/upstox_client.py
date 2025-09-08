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
		url = f"{UPSTOX_BASE_URL}/v2/market/instruments"
		r = self.session.get(url, timeout=30)
		r.raise_for_status()
		data = r.json().get("data") or []
		return data

	def get_historical_candles(self, instrument_key: str, interval: str, from_date: dt.datetime, to_date: dt.datetime) -> List[Dict[str, Any]]:
		url = f"{UPSTOX_BASE_URL}/v2/historical-candle/{instrument_key}/{interval}"
		params = {
			"from_date": from_date.strftime("%Y-%m-%d %H:%M"),
			"to_date": to_date.strftime("%Y-%m-%d %H:%M"),
		}
		r = self.session.get(url, params=params, timeout=30)
		r.raise_for_status()
		return r.json().get("data", {}).get("candles", [])

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