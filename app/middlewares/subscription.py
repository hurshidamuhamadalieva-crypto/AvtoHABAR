import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from app.database import get_or_create_user
from config import config

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseMiddleware):
    """Oddiy (matnli) xabarlar uchun: bloklangan/faolsiz foydalanuvchilarni cheklaydi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        if event.from_user and event.from_user.id in config.ADMIN_IDS:
            return await handler(event, data)

        text = event.text or ""
        if text.startswith("/start") or text.startswith("/menu"):
            return await handler(event, data)

        user = await get_or_create_user(
            telegram_id=event.from_user.id,
            username=event.from_user.username,
            full_name=event.from_user.full_name,
        )

        if user.is_banned:
            await event.answer("🚫 Hisobingiz bloklangan. Murojaat uchun admin bilan bog'laning.")
            return

        if not user.is_active:
            state = data.get("state")
            if state:
                current = await state.get_state()
                if current and ("PaymentFlow" in (current or "") or "PhoneFlow" in (current or "")):
                    return await handler(event, data)

            if not text.startswith("/"):
                await event.answer(
                    "❌ <b>Faol obuna yo'q</b>\n\n"
                    "Botdan foydalanish uchun obuna kerak.\n"
                    "Reja tanlash uchun /start yuboring.",
                    parse_mode="HTML",
                )
                return

        return await handler(event, data)


class BanCheckCallbackMiddleware(BaseMiddleware):
    """Inline tugma bosishlarida (callback_query) bloklangan foydalanuvchini to'xtatadi.

    Obuna talab qiladigan bo'limlar (raqam, guruh, e'lon yuborish) o'z ichida
    alohida tekshiradi — chunki to'lov va admin bilan bog'lanish kabi ba'zi
    tugmalar faol obunasiz ham ishlashi kerak.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        if event.from_user and event.from_user.id in config.ADMIN_IDS:
            return await handler(event, data)

        user = await get_or_create_user(
            telegram_id=event.from_user.id,
            username=event.from_user.username,
            full_name=event.from_user.full_name,
        )

        if user.is_banned:
            await event.answer("🚫 Hisobingiz bloklangan.", show_alert=True)
            return

        callback = event.data or ""
        if callback.startswith(("bc_", "gp:", "gp_", "groups:", "folder:", "phone:agree")) and callback not in ("bc_stop", "bc_cancel", "groups:back"):
            if not user.is_active or not user.session_string and callback.startswith("bc_"):
                await event.answer("Faol obuna va ulangan hisob kerak.", show_alert=True)
                return

        return await handler(event, data)
