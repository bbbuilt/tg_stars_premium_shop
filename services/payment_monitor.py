"""Polling payment monitor for TON and FreeKassa orders."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram import Bot
from loguru import logger

from services.config import BotConfig
from services.db import Database
from services.fragment_api import FragmentAPIService
from services.freekassa import get_freekassa_service
from services.ton import TONService


def _parse_dt(value: Any) -> datetime:
    """Parse SQLite/Python datetime strings used by this demo."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class PaymentMonitor:
    """Checks payments and fulfills paid orders through Fragment API."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.db = Database(config.database_path)
        self.ton = TONService(config.ton_wallet_address, config.toncenter_api_key)
        self.fragment = FragmentAPIService(
            wallet_mnemonic=config.fragment_wallet_mnemonic,
            api_url=config.fragment_api_url,
            api_mode=config.fragment_api_mode,
            payment_method=config.fragment_payment_method,
            cookies_base64=config.fragment_cookies_base64,
        )
        self.bot: Optional[Bot] = None
        self._running = False
        self._stop_event: Optional[asyncio.Event] = None

    def set_bot(self, bot: Bot) -> None:
        """Attach aiogram Bot so the monitor can notify users."""
        self.bot = bot

    async def start(self) -> None:
        """Run payment checks until `stop()` is called."""
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        logger.info("Payment monitor запущен")
        try:
            while self._running:
                await self.tick()
                await self._sleep(30)
        finally:
            self._running = False
            self._stop_event = None
            logger.info("Payment monitor остановлен")

    async def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False
        if self._stop_event:
            self._stop_event.set()

    async def tick(self) -> None:
        """One full monitor pass: check pending payments, then fulfill paid orders."""
        for order in await self.db.get_pending_orders():
            await self.check_payment(order)
        for order in await self.db.get_paid_orders():
            await self.fulfill_paid_order(order)

    async def check_order_now(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Manual one-order check used by the 'I paid' button."""
        order = await self.db.get_order(order_id)
        if not order:
            return None
        if order["status"] == "pending":
            await self.check_payment(order)
        order = await self.db.get_order(order_id)
        if order and order["status"] == "paid":
            await self.fulfill_paid_order(order)
        return await self.db.get_order(order_id)

    async def check_payment(self, order: Dict[str, Any]) -> None:
        """Check a pending TON or FreeKassa order."""
        order_id = int(order["id"])
        expires_at = _parse_dt(order["expires_at"])
        if datetime.now() > expires_at:
            await self.db.set_status(order_id, "expired")
            await self._notify(order["user_id"], f"⏰ Заказ #{order_id} истек без оплаты.")
            return

        payment_type = order.get("payment_type") or "ton"
        if payment_type == "ton":
            await self._check_ton(order)
        elif payment_type == "freekassa":
            await self._check_freekassa(order)

    async def _check_ton(self, order: Dict[str, Any]) -> None:
        order_id = int(order["id"])
        tx = await self.ton.check_transaction(
            comment=order["payment_comment"],
            min_amount=float(order["total_ton"]),
            since_timestamp=int(_parse_dt(order["created_at"]).timestamp()),
        )
        if not tx:
            return

        await self.db.set_status(order_id, "paid", tx_hash=tx["hash"])
        await self._notify(
            order["user_id"],
            (
                f"✅ Оплата TON по заказу #{order_id} найдена.\n"
                "Покупаю Stars через Fragment API..."
            ),
        )

    async def _check_freekassa(self, order: Dict[str, Any]) -> None:
        order_id = int(order["id"])
        payment_id = order.get("freekassa_payment_id")
        if not payment_id or not self.config.freekassa_ready:
            return

        service = get_freekassa_service(self.config)
        payment_data = await service.get_payment_status(str(payment_id))
        if not service.is_payment_successful(payment_data):
            return

        await self.db.set_status(order_id, "paid")
        amount = service.get_payment_amount(payment_data)
        amount_text = f"{amount:.2f} ₽" if amount else "FreeKassa"
        await self._notify(
            order["user_id"],
            (
                f"✅ Оплата {amount_text} по заказу #{order_id} подтверждена.\n"
                "Покупаю Stars через Fragment API..."
            ),
        )

    async def fulfill_paid_order(self, order: Dict[str, Any]) -> None:
        """Deliver Stars through Fragment API after payment confirmation."""
        order_id = int(order["id"])
        if not await self.db.acquire_paid_order(order_id):
            return

        recipient = order.get("recipient_username") or order.get("username")
        try:
            tx_hash = await self.fragment.buy_stars(str(recipient), int(order["stars"]))
            await self.db.set_status(order_id, "completed", fragment_tx_hash=tx_hash)
            await self._notify(
                order["user_id"],
                (
                    f"⭐ Заказ #{order_id} выполнен.\n"
                    f"Получатель: <b>{recipient}</b>\n"
                    f"Stars: <b>{int(order['stars'])}</b>\n"
                    f"Fragment tx: <code>{tx_hash}</code>"
                ),
            )
        except Exception as exc:
            logger.exception("Fragment fulfillment failed for order #{}", order_id)
            await self.db.set_status(order_id, "failed", error_message=str(exc))
            await self._notify(
                order["user_id"],
                (
                    f"⚠️ Заказ #{order_id} оплачен, но Fragment API вернул ошибку.\n"
                    "Администратор должен проверить заказ вручную."
                ),
            )

    async def _notify(self, user_id: int, text: str) -> None:
        if not self.bot:
            return
        try:
            await self.bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Не удалось уведомить пользователя {}: {}", user_id, exc)

    async def _sleep(self, seconds: int) -> None:
        if not self._stop_event:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
