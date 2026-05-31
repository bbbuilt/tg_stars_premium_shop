"""The complete demo order flow: recipient, amount, payment, fulfillment."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from handlers.start import main_menu_markup
from services.config import BotConfig
from services.db import Database
from services.fragment_api import FragmentAPIService
from services.freekassa import get_freekassa_service
from services.payment_monitor import PaymentMonitor
from services.ton import TONService

router = Router()


class BuyStars(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()
    choosing_payment = State()


def _normalize_username(value: str) -> str:
    """Normalize and validate a Telegram username for Fragment."""
    username = value.strip().replace("https://t.me/", "").replace("t.me/", "")
    username = username[1:] if username.startswith("@") else username
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("Введите username без пробелов, например @durov")
    return f"@{username}"


def _money(value: float, suffix: str) -> str:
    return f"{value:.2f} {suffix}"


async def _quote(config: BotConfig, stars: int) -> Dict[str, float]:
    """Calculate demo invoice prices in USD, TON and RUB."""
    fragment = FragmentAPIService(
        wallet_mnemonic=config.fragment_wallet_mnemonic,
        api_url=config.fragment_api_url,
        api_mode=config.fragment_api_mode,
        payment_method=config.fragment_payment_method,
        cookies_base64=config.fragment_cookies_base64,
    )
    ton = TONService(config.ton_wallet_address, config.toncenter_api_key)

    fragment_usd = await fragment.estimate_stars_price_usd(stars)
    total_usd = round(fragment_usd * (1 + config.service_commission_percent / 100), 2)
    total_ton = await ton.usd_to_ton(total_usd)
    total_rub = math.ceil(total_usd * config.usd_rub_rate * 100) / 100
    return {"price_usd": total_usd, "total_ton": total_ton, "total_rub": total_rub}


def _payment_keyboard(config: BotConfig) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Оплатить TON", callback_data="pay_ton")]]
    if config.freekassa_ready:
        rows.append([InlineKeyboardButton(text="Оплатить FreeKassa", callback_data="pay_freekassa")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel_buy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _check_keyboard(order_id: int, payment_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if payment_url:
        rows.append([InlineKeyboardButton(text="Открыть оплату", url=payment_url)])
    rows.append([InlineKeyboardButton(text="Я оплатил", callback_data=f"check_order:{order_id}")])
    rows.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "buy_stars")
async def buy_stars(callback: CallbackQuery, state: FSMContext) -> None:
    """Start the purchase flow."""
    await state.clear()
    await state.set_state(BuyStars.waiting_recipient)

    rows = []
    if callback.from_user and callback.from_user.username:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Купить себе (@{callback.from_user.username})",
                    callback_data="recipient_self",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel_buy")])
    await callback.message.edit_text(
        "Введите username получателя Stars. Пример: <code>@durov</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "recipient_self")
async def recipient_self(callback: CallbackQuery, state: FSMContext) -> None:
    """Use the buyer's public Telegram username as recipient."""
    if not callback.from_user or not callback.from_user.username:
        await callback.answer("У вашего аккаунта нет username.", show_alert=True)
        return
    await state.update_data(recipient_username=f"@{callback.from_user.username}")
    await _ask_amount(callback.message, state)
    await callback.answer()


@router.message(BuyStars.waiting_recipient)
async def recipient_entered(message: Message, state: FSMContext) -> None:
    """Store recipient username."""
    try:
        recipient = _normalize_username(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(recipient_username=recipient)
    await _ask_amount(message, state)


async def _ask_amount(target: Message, state: FSMContext) -> None:
    config = BotConfig.load()
    await state.set_state(BuyStars.waiting_amount)
    await target.answer(
        (
            "Сколько Stars купить?\n"
            f"Минимум: <b>{config.min_stars_amount}</b>, максимум: <b>{config.max_stars_amount}</b>."
        ),
        parse_mode="HTML",
    )


@router.message(BuyStars.waiting_amount)
async def amount_entered(message: Message, state: FSMContext) -> None:
    """Validate amount and show payment choices."""
    config = BotConfig.load()
    raw = (message.text or "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("Введите число, например 100.")
        return

    stars = int(raw)
    if stars < config.min_stars_amount or stars > config.max_stars_amount:
        await message.answer(f"Допустимый диапазон: {config.min_stars_amount} - {config.max_stars_amount}.")
        return

    await message.answer("Считаю сумму...")
    try:
        quote = await _quote(config, stars)
    except Exception as exc:
        logger.exception("Не удалось рассчитать заказ")
        await message.answer(f"Не удалось получить цену: {exc}")
        return

    await state.update_data(stars=stars, **quote)
    data = await state.get_data()
    await state.set_state(BuyStars.choosing_payment)

    text = (
        "<b>Проверьте заказ</b>\n\n"
        f"Получатель: <b>{data['recipient_username']}</b>\n"
        f"Stars: <b>{stars}</b>\n"
        f"Примерная цена: <b>{_money(quote['price_usd'], '$')}</b>\n"
        f"TON: <b>{_money(quote['total_ton'], 'TON')}</b>\n"
    )
    if config.freekassa_ready:
        text += f"FreeKassa: <b>{_money(quote['total_rub'], '₽')}</b>\n"
    text += "\nФинальная покупка Stars выполняется через Fragment API после оплаты."
    await message.answer(text, reply_markup=_payment_keyboard(config), parse_mode="HTML")


@router.callback_query(F.data == "pay_ton")
async def pay_ton(callback: CallbackQuery, state: FSMContext) -> None:
    """Create a TON order and show wallet/comment."""
    config = BotConfig.load()
    data = await _require_order_data(callback, state)
    if not data:
        return

    db = Database(config.database_path)
    ton = TONService(config.ton_wallet_address, config.toncenter_api_key)
    expires_at = datetime.now() + timedelta(minutes=config.order_timeout_minutes)
    order_id = await db.create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username if callback.from_user else None,
        recipient_username=data["recipient_username"],
        stars=int(data["stars"]),
        price_usd=float(data["price_usd"]),
        total_ton=float(data["total_ton"]),
        total_rub=float(data["total_rub"]),
        payment_type="ton",
        payment_address=config.ton_wallet_address,
        payment_comment="",
        expires_at=expires_at,
    )
    comment = ton.generate_payment_comment(order_id)
    await db.update_order(order_id, payment_comment=comment)
    await state.clear()

    text = (
        f"<b>Заказ #{order_id}</b>\n\n"
        f"Отправьте: <b>{_money(float(data['total_ton']), 'TON')}</b>\n"
        f"Адрес: <code>{config.ton_wallet_address}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        "Комментарий обязателен: по нему polling-monitor найдет платеж."
    )
    await callback.message.edit_text(text, reply_markup=_check_keyboard(order_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "pay_freekassa")
async def pay_freekassa(callback: CallbackQuery, state: FSMContext) -> None:
    """Create a FreeKassa order and show payment link."""
    config = BotConfig.load()
    if not config.freekassa_ready:
        await callback.answer("FreeKassa не настроена в .env", show_alert=True)
        return

    data = await _require_order_data(callback, state)
    if not data:
        return

    db = Database(config.database_path)
    expires_at = datetime.now() + timedelta(minutes=config.order_timeout_minutes)
    order_id = await db.create_order(
        user_id=callback.from_user.id,
        username=callback.from_user.username if callback.from_user else None,
        recipient_username=data["recipient_username"],
        stars=int(data["stars"]),
        price_usd=float(data["price_usd"]),
        total_ton=float(data["total_ton"]),
        total_rub=float(data["total_rub"]),
        payment_type="freekassa",
        payment_address="",
        payment_comment="",
        expires_at=expires_at,
    )

    try:
        service = get_freekassa_service(config)
        payment = await service.create_payment(
            order_id=order_id,
            amount_rub=float(data["total_rub"]),
            description=f"Telegram Stars x{int(data['stars'])}",
            email=f"{callback.from_user.id}@telegram.local",
            ip=config.freekassa_customer_ip,
        )
    except Exception as exc:
        await db.set_status(order_id, "failed", error_message=str(exc))
        await callback.message.edit_text(
            f"Не удалось создать платеж FreeKassa для заказа #{order_id}: {exc}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_menu")]]
            ),
        )
        await callback.answer()
        return

    await db.update_order(
        order_id,
        freekassa_payment_id=payment["payment_id"],
        freekassa_payment_url=payment["payment_link"],
    )
    await state.clear()

    text = (
        f"<b>Заказ #{order_id}</b>\n\n"
        f"К оплате FreeKassa: <b>{_money(float(data['total_rub']), '₽')}</b>\n"
        "После оплаты бот сам проверит статус через polling."
    )
    await callback.message.edit_text(
        text,
        reply_markup=_check_keyboard(order_id, payment["payment_link"]),
        parse_mode="HTML",
    )
    await callback.answer()


async def _require_order_data(callback: CallbackQuery, state: FSMContext) -> Dict[str, Any] | None:
    data = await state.get_data()
    required = {"recipient_username", "stars", "price_usd", "total_ton", "total_rub"}
    if not required.issubset(data):
        await callback.answer("Заказ не найден, начните заново.", show_alert=True)
        return None
    return data


@router.callback_query(F.data.startswith("check_order:"))
async def check_order(callback: CallbackQuery) -> None:
    """Manual payment check button."""
    order_id = int(callback.data.split(":", 1)[1])
    config = BotConfig.load()
    monitor = PaymentMonitor(config)
    monitor.set_bot(callback.bot)
    order = await monitor.check_order_now(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    status = order["status"]
    if status == "pending":
        await callback.answer("Оплата пока не найдена. Проверьте сумму и комментарий.", show_alert=True)
    elif status == "processing":
        await callback.answer("Оплата найдена, Fragment API выполняет покупку.", show_alert=True)
    elif status == "completed":
        await callback.message.edit_text(
            (
                f"Заказ #{order_id} выполнен.\n"
                f"Fragment tx: <code>{order.get('fragment_tx_hash')}</code>"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_menu")]]
            ),
            parse_mode="HTML",
        )
        await callback.answer()
    elif status == "failed":
        await callback.answer("Заказ оплачен, но Fragment API вернул ошибку. Проверьте логи.", show_alert=True)
    elif status == "expired":
        await callback.answer("Заказ истек.", show_alert=True)
    else:
        await callback.answer(f"Статус заказа: {status}", show_alert=True)


@router.callback_query(F.data == "my_orders")
async def my_orders(callback: CallbackQuery) -> None:
    """Show a user's latest orders."""
    config = BotConfig.load()
    db = Database(config.database_path)
    orders = await db.get_user_orders(callback.from_user.id)
    if not orders:
        text = "У вас пока нет заказов."
    else:
        lines = ["<b>Последние заказы</b>"]
        for order in orders:
            lines.append(
                (
                    f"#{order['id']} - {order['status']} - "
                    f"{order['stars']} Stars для {order.get('recipient_username') or '-'}"
                )
            )
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_menu")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_buy")
async def cancel_buy(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel FSM flow and return to menu."""
    await state.clear()
    config = BotConfig.load()
    await callback.message.edit_text(
        "Покупка отменена.",
        reply_markup=main_menu_markup(config),
        parse_mode="HTML",
    )
    await callback.answer()
