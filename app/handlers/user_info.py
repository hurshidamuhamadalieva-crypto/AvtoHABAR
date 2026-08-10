import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import get_or_create_user, get_user_payments, get_active_subscription
from app.keyboards import kb_back_to_menu, kb_contact_admin
from config import config

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu:payments")
async def cb_my_payments(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    if not user.is_active:
        await call.answer("❌ Faol obuna yo'q.", show_alert=True)
        return

    sub = await get_active_subscription(user.id)
    payments = await get_user_payments(user.id)

    lines = []

    if sub:
        days_left = max(0, (sub.expires_at - datetime.utcnow()).days) if sub.expires_at else 0
        lines.append(
            f"📋 <b>Joriy Obuna</b>\n"
            f"  Reja: <b>{sub.plan_name}</b>\n"
            f"  Faollashtirilgan: <b>{sub.activated_at.strftime('%Y-%m-%d') if sub.activated_at else 'N/A'}</b>\n"
            f"  Tugaydi: <b>{sub.expires_at.strftime('%Y-%m-%d') if sub.expires_at else 'N/A'}</b>\n"
            f"  Qolgan kunlar: <b>{days_left} kun</b>"
        )
    else:
        lines.append("📋 <b>Faol obuna yo'q</b>")

    if payments:
        lines.append("\n\n💳 <b>To'lovlar tarixi:</b>")
        for p in payments[:10]:
            status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(p.status, "❓")
            method_name = {"admin": "Admin", "card": "Karta"}.get(p.method, p.method)
            status_uz = {"approved": "Tasdiqlandi", "rejected": "Rad etildi", "pending": "Kutilmoqda"}.get(p.status, p.status)
            lines.append(
                f"\n{status_emoji} <b>{p.amount:,} so'm</b> — {method_name}\n"
                f"   📅 {p.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"   Holat: {status_uz}"
            )
    else:
        lines.append("\n\n💳 <i>To'lovlar tarixi yo'q.</i>")

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb_back_to_menu())
    await call.answer()


@router.callback_query(F.data == "menu:contact_admin")
async def cb_contact_admin(call: CallbackQuery, state: FSMContext):
    admin_username = config.ADMIN_USERNAME
    if admin_username:
        await call.message.edit_text(
            "👨‍💼 <b>Admin bilan bog'lanish</b>\n\n"
            "Quyidagi tugmani bosib admin bilan chatni to'g'ridan-to'g'ri oching:",
            parse_mode="HTML",
            reply_markup=kb_contact_admin()
        )
        await call.answer()
        return

    for admin_id in config.ADMIN_IDS:
        try:
            chat = await call.bot.get_chat(admin_id)
            if chat.username:
                from aiogram.utils.keyboard import InlineKeyboardBuilder
                builder = InlineKeyboardBuilder()
                builder.button(text="👨‍💼 Admin chatini ochish", url=f"https://t.me/{chat.username.lstrip('@')}")
                builder.button(text="🔙 Asosiy menyu", callback_data="menu:home")
                builder.adjust(1)
                await call.message.edit_text(
                    "👨‍💼 <b>Admin bilan bog'lanish</b>",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
                await call.answer()
                return
        except Exception:
            pass

    await call.message.edit_text(
        "👨‍💼 <b>Admin</b>\n\n"
        "To'lov arizasi yuborish uchun /start buyrug'ini ishlating.",
        parse_mode="HTML",
        reply_markup=kb_back_to_menu()
    )
    await call.answer()
