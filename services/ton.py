"""Minimal TON receiving helpers.

The demo only accepts incoming TON payments. It does not send TON and therefore
does not need wallet signing code.
"""

from __future__ import annotations

import math
import secrets
import string
import time
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger


class TONService:
    """Read TON/USD price and search incoming payments by comment."""

    def __init__(self, wallet_address: str, toncenter_api_key: Optional[str] = None):
        self.wallet_address = wallet_address
        self.toncenter_api_key = toncenter_api_key
        self._rate_cache: Dict[str, Any] = {"value": None, "timestamp": 0.0}

    def generate_payment_comment(self, order_id: int) -> str:
        """Create a unique payment comment for a single order."""
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        return f"STARS-{order_id}-{suffix}"

    async def get_ton_usd_rate(self) -> float:
        """Return TON/USD rate with a short in-memory cache."""
        now = time.time()
        cached = self._rate_cache["value"]
        if cached and now - self._rate_cache["timestamp"] < 300:
            return float(cached)

        sources = [
            ("Binance", "https://api.binance.com/api/v3/ticker/price", {"symbol": "TONUSDT"}),
            (
                "CoinGecko",
                "https://api.coingecko.com/api/v3/simple/price",
                {"ids": "the-open-network", "vs_currencies": "usd"},
            ),
        ]
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name, url, params in sources:
                try:
                    async with session.get(url, params=params) as response:
                        if response.status != 200:
                            logger.warning("{} вернул HTTP {}", name, response.status)
                            continue
                        data = await response.json()
                        if name == "Binance":
                            rate = float(data["price"])
                        else:
                            rate = float(data["the-open-network"]["usd"])
                        if 0.1 < rate < 100:
                            self._rate_cache = {"value": rate, "timestamp": now}
                            return rate
                except Exception as exc:
                    logger.warning("{} недоступен: {}", name, exc)

        raise RuntimeError("Не удалось получить курс TON/USD")

    async def usd_to_ton(self, usd_amount: float) -> float:
        """Convert USD invoice amount to TON and round up to 0.01 TON."""
        rate = await self.get_ton_usd_rate()
        return math.ceil((usd_amount / rate) * 100) / 100

    async def check_transaction(
        self,
        *,
        comment: str,
        min_amount: float,
        since_timestamp: int,
    ) -> Optional[Dict[str, Any]]:
        """Find an incoming transaction to the configured wallet by amount/comment."""
        params = {"address": self.wallet_address, "limit": 20}
        if self.toncenter_api_key:
            params["api_key"] = self.toncenter_api_key

        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://toncenter.com/api/v2/getTransactions",
                params=params,
                headers={"User-Agent": "Fragment Stars Demo Bot"},
            ) as response:
                if response.status != 200:
                    logger.warning("TonCenter вернул HTTP {}", response.status)
                    return None
                data = await response.json()

        if not data.get("ok"):
            logger.warning("TonCenter error: {}", data)
            return None

        for tx in data.get("result", []):
            if int(tx.get("utime", 0)) < since_timestamp:
                continue
            incoming = tx.get("in_msg") or {}
            amount = int(incoming.get("value") or 0) / 1_000_000_000
            message = incoming.get("message") or ""
            if amount >= min_amount and comment in message:
                return {
                    "hash": tx.get("hash", ""),
                    "amount": amount,
                    "timestamp": tx.get("utime"),
                    "from_address": incoming.get("source"),
                }
        return None
