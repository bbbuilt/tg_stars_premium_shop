"""Start, menu and help handlers."""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.config import BotConfig

router = Router()


def main_menu_markup(config: BotConfig) -> InlineKeyboardMarkup:
    """Build the tiny demo menu."""
    rows = [
        [InlineKeyboardButton(text="Купить Stars", callback_data="buy_stars")],
        [InlineKeyboardButton(text="Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton(text="Справка", callback_data="help")],
    ]
    if config.freekassa_ready:
        rows.append([InlineKeyboardButton(text="FreeKassa включена", callback_data="help_freekassa")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def welcome_text(config: BotConfig) -> str:
    """Short text that explains what this repository demonstrates."""
    return (
        "<b>Fragment Stars demo bot</b>\n\n"
        "Это минимальный пример: polling, оплата в TON, один эквайринг FreeKassa "
        "и покупка Stars через Fragment API.\n\n"
        f"Режим Fragment API: <b>{config.fragment_api_mode}</b>\n"
        "Для реального использования рекомендуется <b>KYC режим</b>: он стабильнее, "
        "потому что API работает с вашей авторизованной Fragment-сессией."
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Show the main menu."""
    config = BotConfig.load()
    await message.answer(welcome_text(config), reply_markup=main_menu_markup(config), parse_mode="HTML")


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery) -> None:
    """Return to the main menu."""
    config = BotConfig.load()
    await callback.message.edit_text(welcome_text(config), reply_markup=main_menu_markup(config), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery) -> None:
    """Explain the happy path without product clutter."""
    config = BotConfig.load()
    text = (
        "<b>Как работает пример</b>\n\n"
        "1. Пользователь вводит username получателя и количество Stars.\n"
        "2. Бот рассчитывает примерную сумму.\n"
        "3. Пользователь платит TON или через FreeKassa.\n"
        "4. Polling-monitor находит оплату.\n"
        "5. Бот вызывает Fragment API и покупает Stars.\n\n"
        "<b>Важно про Fragment API</b>\n"
        "KYC режим стабильнее no_kyc. Для него заполните FRAGMENT_API_MODE=kyc "
        "и FRAGMENT_COOKIES_BASE64 из авторизованной Fragment-сессии."
    )
    if config.support_username:
        text += f"\n\nПоддержка: @{config.support_username}"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back_to_menu")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "help_freekassa")
async def help_freekassa(callback: CallbackQuery) -> None:
    """Small note for the only acquiring provider."""
    await callback.answer("FreeKassa используется только через polling проверки статуса.", show_alert=True)
