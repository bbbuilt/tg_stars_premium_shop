"""The only acquiring provider kept in the demo: FreeKassa.

No webhooks are required here. The bot creates a payment link and checks status
by polling the FreeKassa API, which keeps local setup simple.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from services.config import BotConfig


class FreeKassaService:
    """Small FreeKassa API adapter for payment links and status checks."""

    def __init__(self, config: BotConfig):
        if not config.freekassa_ready:
            raise ValueError("FreeKassa не настроена")
        self.api_key = config.freekassa_api_key or ""
        self.shop_id = config.freekassa_shop_id
        self.method = config.freekassa_method
        self.api_base = "https://api.fk.life/v1"

    def _signature(self, payload: Dict[str, Any]) -> str:
        """Create FreeKassa HMAC-SHA256 signature from sorted payload values."""
        values = []
        for key in sorted(k for k in payload if k != "signature"):
            value = payload[key]
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            values.append(str(value))
        source = "|".join(values)
        return hmac.new(self.api_key.encode(), source.encode(), hashlib.sha256).hexdigest()

    async def create_payment(
        self,
        *,
        order_id: int,
        amount_rub: float,
        description: str,
        email: str,
        ip: str,
    ) -> Dict[str, Any]:
        """Create a FreeKassa payment and return `{payment_id, payment_link}`."""
        amount = round(amount_rub, 2)
        if amount == int(amount):
            amount = int(amount)

        payload: Dict[str, Any] = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
            "paymentId": str(order_id),
            "i": self.method,
            "email": email,
            "ip": ip,
            "amount": amount,
            "currency": "RUB",
            "description": description[:250],
        }
        payload["signature"] = self._signature(payload)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
        }
        url = f"{self.api_base}/orders/create"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                text = await response.text()
                location = response.headers.get("Location")
                try:
                    data = await response.json()
                except Exception:
                    data = {"raw": text}

        if response.status != 200:
            raise RuntimeError(data.get("message") or data.get("error") or text[:300])

        payment_link = location or data.get("location") or data.get("url")
        if not payment_link:
            raise RuntimeError(f"FreeKassa не вернула ссылку оплаты: {data}")

        logger.info("FreeKassa платеж создан: order={}, amount={} RUB", order_id, amount)
        return {"payment_id": str(order_id), "payment_link": payment_link, "raw": data}

    async def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Ask FreeKassa for current payment status."""
        payload: Dict[str, Any] = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
            "paymentId": str(payment_id),
        }
        payload["signature"] = self._signature(payload)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{self.api_base}/orders/status", json=payload, headers=headers) as response:
                if response.status != 200:
                    logger.warning("FreeKassa status HTTP {}", response.status)
                    return None
                return await response.json()

    @staticmethod
    def is_payment_successful(payment_data: Optional[Dict[str, Any]]) -> bool:
        """Return True when FreeKassa says the invoice is paid."""
        status = str((payment_data or {}).get("status", "")).upper()
        return status in {"PAID", "CONFIRMED", "SUCCESS", "COMPLETED", "2"}

    @staticmethod
    def get_payment_amount(payment_data: Optional[Dict[str, Any]]) -> Optional[float]:
        """Extract amount from FreeKassa response if present."""
        try:
            return float((payment_data or {}).get("amount"))
        except (TypeError, ValueError):
            return None


def get_freekassa_service(config: BotConfig) -> FreeKassaService:
    """Factory kept as a named function so handlers stay readable."""
    return FreeKassaService(config)
