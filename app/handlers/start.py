import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import get_or_create_user, get_active_subscription
from app.keyboards import kb_plans, kb_main_menu
from config import config

router = Router()
logger = logging.getLogger(__name__)

START_TEXT = """
🤖 <b>AutoAd Bot ga xush kelibsiz!</b>

E'loningizni yuzlab Telegram guruhlarga avtomatik, xavfsiz va professional tarzda yuboring.

<b>✅ Bot imkoniyatlari:</b>
• 📨 Tanlangan barcha guruhlarga avtomatik yuborish
• ⏱ Moslashtirilgan yuborish intervali (7–20 daqiqa)
• 📁 Telegram jildlaridan guruhlarni import qilish
• 🔄 Siz ish bilan band bo'lganingizda uzluksiz yuborish
• 🔒 Xavfsiz — o'z Telegram hisobingiz orqali ishlaydi
• 📊 Har bir yuborilgan xabar statistikasi

<b>💼 Obuna rejalari:</b>
"""


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    if user.is_banned:
        await message.answer(
            "🚫 <b>Kirish taqiqlangan</b>\n\n"
            "Hisobingiz bloklangan. Murojaat uchun qo'llab-quvvatlash xizmatiga yozing.",
            parse_mode="HTML"
        )
        return

    if user.is_active:
        sub = await get_active_subscription(user.id)
        days_left = 0
        if sub and sub.expires_at:
            days_left = max(0, (sub.expires_at - datetime.utcnow()).days)

        await message.answer(
            f"👋 <b>Xush kelibsiz, {message.from_user.first_name}!</b>\n\n"
            f"✅ Obuna: <b>{sub.plan_name if sub else 'Faol'}</b>\n"
            f"📅 Qolgan kunlar: <b>{days_left} kun</b>\n\n"
            "Quyidagi menyudan kerakli bo'limni tanlang:",
            parse_mode="HTML",
            reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string)),
        )
        return

    plan_lines = "\n".join(
        f"  {p['name']} — <b>{p['price']:,} so'm</b> ({p['days']} kun)"
        for p in config.PLANS.values()
    )

    await message.answer(
        START_TEXT + plan_lines + "\n\n👇 <b>Obuna rejasini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=kb_plans(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    if user.is_banned:
        await message.answer("🚫 Hisobingiz bloklangan.", parse_mode="HTML")
        return

    if not user.is_active:
        await message.answer(
            "❌ <b>Faol obuna yo'q.</b>\n\nReja tanlash uchun /start buyrug'ini yuboring.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "📋 <b>Asosiy Menyu</b>\n\nKerakli bo'limni tanlang:",
        parse_mode="HTML",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string)),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(call.from_user.id)
    text = "📋 <b>Asosiy Menyu</b>\n\nKerakli bo'limni tanlang:"
    markup = kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)
    await call.answer()
