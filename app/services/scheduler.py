import asyncio
import logging
from datetime import datetime

from app.database import (
    get_expiring_subscriptions, get_expired_subscriptions,
    deactivate_subscription, mark_reminder_sent, get_user_by_db_id
)

logger = logging.getLogger(__name__)


async def subscription_checker(bot):
    """Har soatda obunalarni tekshirish: eslatma yuborish va muddati o'tganlarini o'chirish."""
    while True:
        try:
            await _send_expiry_reminders(bot)
            await _deactivate_expired(bot)
        except Exception as e:
            logger.error(f"Obuna tekshiruvida xato: {e}", exc_info=True)
        await asyncio.sleep(3600)


async def _send_expiry_reminders(bot):
    subs = await get_expiring_subscriptions()
    for sub in subs:
        try:
            user = await get_user_by_db_id(sub.user_id)
            if user:
                await bot.send_message(
                    user.telegram_id,
                    "⚠️ <b>Obuna muddati tugayapti!</b>\n\n"
                    f"📅 <b>{sub.plan_name}</b> obunangiz <b>ertaga</b> tugaydi.\n\n"
                    "Uzluksiz foydalanishni davom ettirish uchun admin bilan bog'laning va obunani yangilang.",
                    parse_mode="HTML"
                )
                await mark_reminder_sent(sub.id)
                logger.info(f"Eslatma yuborildi: foydalanuvchi {user.telegram_id}")
        except Exception as e:
            logger.error(f"Obuna {sub.id} uchun eslatma yuborishda xato: {e}")


async def _deactivate_expired(bot):
    subs = await get_expired_subscriptions()
    for sub in subs:
        try:
            user = await get_user_by_db_id(sub.user_id)
            await deactivate_subscription(sub.id, sub.user_id)
            if user:
                await bot.send_message(
                    user.telegram_id,
                    "🔴 <b>Obuna Muddati Tugadi</b>\n\n"
                    f"<b>{sub.plan_name}</b> obunangiz tugadi.\n\n"
                    "Kirishingiz to'xtatildi. Davom ettirish uchun obunani yangilang.\n"
                    "Yangi reja tanlash uchun /start yuboring.",
                    parse_mode="HTML"
                )
                logger.info(f"Obuna muddati tugadi: foydalanuvchi {user.telegram_id}")
        except Exception as e:
            logger.error(f"Obuna {sub.id} ni o'chirishda xato: {e}")
