import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
	api_key: str
	api_secret: str
	redirect_uri: str
	access_token: str | None
	refresh_token: str | None
	environment: str
	account_balance: float
	stop_loss_rupees: float
	order_product: str
	order_variety: str


def get_settings() -> Settings:
	api_key = os.getenv("UPSTOX_API_KEY", "").strip()
	api_secret = os.getenv("UPSTOX_API_SECRET", "").strip()
	redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
	access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
	refresh_token = os.getenv("UPSTOX_REFRESH_TOKEN")
	environment = os.getenv("UPSTOX_ENV", "live").strip()
	account_balance = float(os.getenv("ACCOUNT_BALANCE", "50000"))
	stop_loss_rupees = float(os.getenv("STOP_LOSS_RUPEES", "5000"))
	order_product = os.getenv("ORDER_PRODUCT", "MIS").strip()
	order_variety = os.getenv("ORDER_VARIETY", "REGULAR").strip()

	if not api_key or not api_secret:
		raise ValueError("Missing UPSTOX_API_KEY or UPSTOX_API_SECRET in environment")

	return Settings(
		api_key=api_key,
		api_secret=api_secret,
		redirect_uri=redirect_uri,
		access_token=access_token,
		refresh_token=refresh_token,
		environment=environment,
		account_balance=account_balance,
		stop_loss_rupees=stop_loss_rupees,
		order_product=order_product,
		order_variety=order_variety,
	)