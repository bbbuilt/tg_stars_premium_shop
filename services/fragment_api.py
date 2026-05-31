"""A small, documented wrapper around `bbbuilt/fragment-stars-api`.

SDK repository: https://github.com/bbbuilt/fragment-stars-api

This file is the main point of the repository: it shows the minimum calls you
need for Fragment API integration:

1. `get_rates()` to check API availability and commission mode.
2. `get_prices()` to show current TON / USDT-on-TON Stars prices.
3. `buy_stars()` to deliver Stars after a confirmed payment.

KYC mode is the recommended path for stable production work because the API can
reuse your authenticated Fragment session cookies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any, Dict, Optional

from loguru import logger

try:
    from fragment_api import FragmentAPIClient, FragmentAPIError as LibraryFragmentAPIError

    FRAGMENT_LIBRARY_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on local env
    FragmentAPIClient = None
    LibraryFragmentAPIError = Exception
    FRAGMENT_LIBRARY_AVAILABLE = False


class FragmentAPIError(Exception):
    """Raised when the Fragment API client cannot complete an operation."""


def _value(source: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a dict response or a model-like object."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


class FragmentAPIService:
    """Thin async adapter for the synchronous `bbbuilt/fragment-stars-api` client."""

    def __init__(
        self,
        *,
        wallet_mnemonic: str,
        api_url: str,
        api_mode: str,
        payment_method: str,
        cookies_base64: Optional[str],
    ):
        self.wallet_mnemonic_base64 = self._as_base64_seed(wallet_mnemonic)
        self.api_url = api_url
        self.api_mode = api_mode
        self.payment_method = payment_method
        self.cookies_base64 = self._validated_cookies(cookies_base64) if api_mode == "kyc" else None
        self.client = FragmentAPIClient(base_url=api_url) if FRAGMENT_LIBRARY_AVAILABLE else None

        if not FRAGMENT_LIBRARY_AVAILABLE:
            logger.error(
                "Пакет bbbuilt/fragment-stars-api не установлен. "
                "Репозиторий SDK: https://github.com/bbbuilt/fragment-stars-api"
            )
        elif api_mode == "kyc" and self.cookies_base64:
            logger.info(
                "Fragment API работает в KYC режиме: payment_method={}, комиссия API 0%.",
                self.payment_method,
            )
        elif api_mode == "kyc":
            logger.warning("Выбран KYC режим, но cookies пустые. Добавьте FRAGMENT_COOKIES_BASE64.")
        else:
            logger.warning(
                "Fragment API в no_kyc режиме: payment_method={}. Это удобно для теста, но менее стабильно.",
                self.payment_method,
            )

    @staticmethod
    def _as_base64_seed(seed: str) -> str:
        """The API expects the 24-word seed phrase encoded as Base64."""
        seed = (seed or "").strip()
        if not seed:
            raise FragmentAPIError("FRAGMENT_WALLET_MNEMONIC пустой")

        try:
            decoded = base64.b64decode(seed).decode("utf-8")
            if len(decoded.split()) == 24:
                return seed
        except Exception:
            pass
        return base64.b64encode(seed.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _validated_cookies(cookies_base64: Optional[str]) -> Optional[str]:
        """Validate that KYC cookies are Base64 JSON, but pass them to API as Base64."""
        if not cookies_base64:
            return None
        try:
            decoded = base64.b64decode(cookies_base64).decode("utf-8")
            parsed = json.loads(decoded)
            if not isinstance(parsed, dict):
                raise ValueError("cookies JSON должен быть объектом")
        except Exception as exc:
            logger.warning("FRAGMENT_COOKIES_BASE64 не похож на Base64 JSON: {}", exc)
            return cookies_base64
        return cookies_base64

    async def check_health(self) -> Dict[str, Any]:
        """Call `get_rates()` once and return a compact health result."""
        started = time.monotonic()
        try:
            rates = await self.get_rates()
            return {
                "ok": True,
                "rates": rates,
                "response_time": round(time.monotonic() - started, 2),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "response_time": round(time.monotonic() - started, 2),
            }

    async def get_rates(self) -> Dict[str, float]:
        """Return Fragment commission rates for both modes."""
        if not self.client:
            raise FragmentAPIError("Fragment API client не инициализирован")

        response = await asyncio.to_thread(self.client.get_rates)
        no_kyc_percent = float(_value(response, "rate_no_kyc", 5.0))
        kyc_percent = float(_value(response, "rate_with_kyc", 3.0))
        no_kyc_decimal = float(_value(response, "rate_no_kyc_decimal", no_kyc_percent / 100))
        kyc_decimal = float(_value(response, "rate_with_kyc_decimal", kyc_percent / 100))

        return {
            "no_kyc_percent": no_kyc_percent,
            "kyc_percent": kyc_percent,
            "no_kyc_decimal": no_kyc_decimal,
            "kyc_decimal": kyc_decimal,
        }

    async def get_prices(self) -> Dict[str, Any]:
        """Return current API prices including TON and USDT-on-TON fields."""
        if not self.client:
            raise FragmentAPIError("Fragment API client не инициализирован")
        return await asyncio.to_thread(self.client.get_prices)

    async def estimate_stars_price_usd(self, stars_count: int) -> float:
        """Estimate USD price for checkout before the real Fragment purchase.

        Fragment charges the real TON or USDT-on-TON amount during `buy_stars()`.
        For a demo bot we only need a predictable pre-payment invoice, so the
        base star price is read from API prices when possible and falls back to
        `FRAGMENT_STAR_BASE_USD`.
        """
        rates = await self.get_rates()
        base_usd_per_star = float(os.getenv("FRAGMENT_STAR_BASE_USD", "0.015"))
        try:
            prices = await self.get_prices()
            stars = prices.get("stars", {}) if isinstance(prices, dict) else {}
            # USDT-on-TON price is effectively a USD quote, so it is ideal for fiat invoices.
            if self.payment_method == "usdt_ton" and stars.get("price_per_star_usdt_ton"):
                base_usd_per_star = float(stars["price_per_star_usdt_ton"])
            elif stars.get("price_per_star_usdt_ton"):
                base_usd_per_star = float(stars["price_per_star_usdt_ton"])
        except Exception as exc:
            logger.warning("Не удалось получить live prices, использую FRAGMENT_STAR_BASE_USD: {}", exc)
        mode_decimal = rates["kyc_decimal"] if self.api_mode == "kyc" else rates["no_kyc_decimal"]
        return round(stars_count * base_usd_per_star * (1 + mode_decimal), 2)

    async def buy_stars(self, recipient_username: str, stars_count: int) -> str:
        """Buy Stars for `recipient_username` and return Fragment transaction id/hash."""
        if not self.client:
            raise FragmentAPIError("Fragment API client не инициализирован")

        username = recipient_username.strip()
        if not username:
            raise FragmentAPIError("recipient_username пустой")
        if not username.startswith("@"):
            username = f"@{username}"

        logger.info(
            "Fragment buy_stars: username={}, amount={}, mode={}, payment_method={}",
            username,
            stars_count,
            self.api_mode,
            self.payment_method,
        )

        try:
            result = await asyncio.to_thread(
                self.client.buy_stars,
                username=username,
                amount=stars_count,
                seed=self.wallet_mnemonic_base64,
                cookies=self.cookies_base64,
                payment_method=self.payment_method,
                wait=True,
            )
        except LibraryFragmentAPIError as exc:
            raise FragmentAPIError(str(exc)) from exc
        except Exception as exc:
            raise FragmentAPIError(f"Ошибка Fragment API: {exc}") from exc

        success = _value(result, "success")
        if success is False:
            raise FragmentAPIError(_value(result, "error", "Fragment API вернул success=false"))

        tx_hash = (
            _value(result, "transaction_hash")
            or _value(result, "transaction_id")
            or _value(result, "request_id")
            or _value(result, "id")
        )
        if not tx_hash and isinstance(result, str):
            tx_hash = result
        if not tx_hash:
            raise FragmentAPIError(f"Не удалось найти transaction id в ответе: {result}")

        logger.info("Fragment purchase complete: {}", tx_hash)
        return str(tx_hash)


# Backward-compatible alias for old imports that users may still try.
FragmentService = FragmentAPIService
