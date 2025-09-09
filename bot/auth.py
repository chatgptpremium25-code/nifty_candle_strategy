from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .config import get_settings


UPSTOX_BASE_URL = "https://api.upstox.com"


@dataclass
class TokenBundle:
	access_token: str
	refresh_token: Optional[str]
	expires_in: Optional[int]
	token_type: Optional[str]
	scopes: Optional[str]
	obtained_at_epoch_s: float

	def is_expired(self) -> bool:
		if not self.expires_in:
			return False
		return time.time() >= (self.obtained_at_epoch_s + max(0, self.expires_in - 30))


class UpstoxAuth:
	def __init__(self, tokens_path: str | Path = "tokens.json") -> None:
		self.settings = get_settings()
		self.tokens_path = Path(tokens_path)
		self.tokens: Optional[TokenBundle] = None

	def get_login_url(self, scopes: Optional[list[str]] = None, state: str = "state") -> str:
		if scopes is None:
			scopes = [
				"marketdata",
				"orders",
				"portfolio",
			]
		params = {
			"response_type": "code",
			"client_id": self.settings.api_key,
			"redirect_uri": self.settings.redirect_uri,
			"scope": " ".join(scopes),
			"state": state,
		}
		return f"{UPSTOX_BASE_URL}/v2/login/authorization/dialog?{urllib.parse.urlencode(params)}"

	def exchange_code_for_token(self, code: str) -> TokenBundle:
		url = f"{UPSTOX_BASE_URL}/v2/login/authorization/token"
		data = {
			"code": code,
			"client_id": self.settings.api_key,
			"client_secret": self.settings.api_secret,
			"redirect_uri": self.settings.redirect_uri,
			"grant_type": "authorization_code",
		}
		r = requests.post(url, data=data, timeout=15)
		r.raise_for_status()
		payload = r.json().get("data") or r.json()
		bundle = TokenBundle(
			access_token=payload["access_token"],
			refresh_token=payload.get("refresh_token"),
			expires_in=payload.get("expires_in"),
			token_type=payload.get("token_type"),
			scopes=payload.get("scope"),
			obtained_at_epoch_s=time.time(),
		)
		self.save_tokens(bundle)
		return bundle

	def refresh_access_token(self) -> TokenBundle:
		if not self.tokens or not self.tokens.refresh_token:
			raise RuntimeError("No refresh token available. Run initial auth first.")
		url = f"{UPSTOX_BASE_URL}/v2/login/authorization/token"
		data = {
			"refresh_token": self.tokens.refresh_token,
			"client_id": self.settings.api_key,
			"client_secret": self.settings.api_secret,
			"grant_type": "refresh_token",
		}
		r = requests.post(url, data=data, timeout=15)
		r.raise_for_status()
		payload = r.json().get("data") or r.json()
		bundle = TokenBundle(
			access_token=payload["access_token"],
			refresh_token=payload.get("refresh_token", self.tokens.refresh_token),
			expires_in=payload.get("expires_in"),
			token_type=payload.get("token_type"),
			scopes=payload.get("scope"),
			obtained_at_epoch_s=time.time(),
		)
		self.save_tokens(bundle)
		return bundle

	def load_tokens(self) -> Optional[TokenBundle]:
		if self.tokens_path.exists():
			data = json.loads(self.tokens_path.read_text())
			self.tokens = TokenBundle(
				access_token=data["access_token"],
				refresh_token=data.get("refresh_token"),
				expires_in=data.get("expires_in"),
				token_type=data.get("token_type"),
				scopes=data.get("scope"),
				obtained_at_epoch_s=data.get("obtained_at_epoch_s", time.time()),
			)
			return self.tokens
		return None

	def save_tokens(self, bundle: TokenBundle) -> None:
		self.tokens = bundle
		self.tokens_path.write_text(json.dumps({
			"access_token": bundle.access_token,
			"refresh_token": bundle.refresh_token,
			"expires_in": bundle.expires_in,
			"token_type": bundle.token_type,
			"scope": bundle.scopes,
			"obtained_at_epoch_s": bundle.obtained_at_epoch_s,
		}, indent=2))

	def get_authorized_session(self) -> requests.Session:
		if not self.tokens:
			self.load_tokens()
		if not self.tokens:
			# fall back to env access token if provided
			if self.settings.access_token:
				self.tokens = TokenBundle(
					access_token=self.settings.access_token,
					refresh_token=self.settings.refresh_token,
					expires_in=None,
					token_type="Bearer",
					scopes=None,
					obtained_at_epoch_s=time.time(),
				)
			else:
				raise RuntimeError("No access token available. Run auth or set UPSTOX_ACCESS_TOKEN.")
		if self.tokens.is_expired():
			self.refresh_access_token()
		s = requests.Session()
		s.headers.update({
			"Authorization": f"Bearer {self.tokens.access_token}",
			"Content-Type": "application/json",
		})
		return s