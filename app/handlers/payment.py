import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import (
    get_or_create_user, create_payment, get_payment, update_payment,
    activate_subscription
)
from app.keyboards import (
    kb_plans, kb_payment_methods, kb_main_menu, kb_approve_payment,
    kb_back_inline
)
from app.states import PaymentFlow
from config import config

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("plan:"))
async def cb_select_plan(call: CallbackQuery, state: FSMContext):
    plan_key = call.data.split(":")[1]
    plan = config.PLANS.get(plan_key)
    if not plan:
        await call.answer("Noto'g'ri reja.", show_alert=True)
        return

    await state.update_data(plan_key=plan_key)
    await state.set_state(PaymentFlow.choosing_method)

    text = (
        f"✅ <b>Tanlangan Reja: {plan['name']}</b>\n\n"
        f"💰 Narx: <b>{plan['price']:,} so'm</b>\n"
        f"📅 Muddat: <b>{plan['days']} kun</b>\n\n"
        "To'lov usulini tanlang:"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb_payment_methods())
    await call.answer()


@router.callback_query(F.data == "pay:back")
async def cb_back_to_plans(call: CallbackQuery, state: FSMContext):
    await state.clear()
    plan_lines = "\n".join(
        f"  {p['name']} — <b>{p['price']:,} so'm</b> ({p['days']} kun)"
        for p in config.PLANS.values()
    )
    await call.message.edit_text(
        "👇 <b>Obuna rejasini tanlang:</b>\n\n" + plan_lines,
        parse_mode="HTML",
        reply_markup=kb_plans()
    )
    await call.answer()


@router.callback_query(F.data == "pay:back_method")
async def cb_back_to_method(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("plan_key")
    plan = config.PLANS.get(plan_key, {})
    text = (
        f"✅ <b>Tanlangan Reja: {plan.get('name', '')}</b>\n\n"
        f"💰 Narx: <b>{plan.get('price', 0):,} so'm</b>\n"
        f"📅 Muddat: <b>{plan.get('days', 0)} kun</b>\n\n"
        "To'lov usulini tanlang:"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb_payment_methods())
    await call.answer()


# ─── Admin orqali to'lash ─────────────────────────────────────────────────────

@router.callback_query(F.data == "pay:admin")
async def cb_pay_admin(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("plan_key")
    plan = config.PLANS.get(plan_key)
    if not plan:
        await call.answer("Sessiya muddati o'tdi. Qayta boshlang.", show_alert=True)
        return

    user = await get_or_create_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    payment = await create_payment(user.id, plan_key, plan["price"], "admin")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="👨‍💼 Admin bilan bog'lanish", url=f"https://t.me/{config.ADMIN_USERNAME}")
    builder.button(text="🔙 Orqaga", callback_data="pay:back_method")
    builder.adjust(1)

    await call.message.edit_text(
        f"📋 <b>Ariza Yuborildi</b>\n\n"
        f"Reja: <b>{plan['name']}</b>\n"
        f"Summa: <b>{plan['price']:,} so'm</b>\n"
        f"Usul: <b>Admin orqali</b>\n\n"
        "👨‍💼 To'lovni amalga oshirish uchun admin bilan bog'laning.\n"
        "Tasdiqlangach, xabar olasiz.\n\n"
        "⏳ <i>Admin tasdiqlashi kutilmoqda...</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await call.bot.send_message(
                admin_id,
                f"🆕 <b>Yangi To'lov Arizasi</b>\n\n"
                f"👤 Foydalanuvchi: <a href='tg://user?id={call.from_user.id}'>{call.from_user.full_name}</a>\n"
                f"🆔 ID: <code>{call.from_user.id}</code>\n"
                f"📋 Reja: <b>{plan['name']}</b>\n"
                f"💰 Summa: <b>{plan['price']:,} so'm</b>\n"
                f"💳 Usul: <b>Admin orqali</b>\n"
                f"🕐 Vaqt: <b>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</b>",
                parse_mode="HTML",
                reply_markup=kb_approve_payment(payment.id, call.from_user.id)
            )
        except Exception as e:
            logger.error(f"Admin {admin_id} ga xabar yuborishda xato: {e}")

    await call.answer("Ariza yuborildi!")


# ─── Karta orqali to'lash ─────────────────────────────────────────────────────

@router.callback_query(F.data == "pay:card")
async def cb_pay_card(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("plan_key")
    plan = config.PLANS.get(plan_key)
    if not plan:
        await call.answer("Sessiya muddati o'tdi.", show_alert=True)
        return

    user = await get_or_create_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    payment = await create_payment(user.id, plan_key, plan["price"], "card")
    await state.update_data(payment_id=payment.id)
    await state.set_state(PaymentFlow.waiting_screenshot)

    await call.message.edit_text(
        f"🏦 <b>Karta Orqali To'lash</b>\n\n"
        f"📋 Reja: <b>{plan['name']}</b>\n"
        f"💰 Summa: <b>{plan['price']:,} so'm</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Karta raqami:\n<code>{config.CARD_NUMBER}</code>\n\n"
        f"👤 Karta egasi:\n<b>{config.CARD_HOLDER}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Aynan shu summani o'tkazing\n"
        "2️⃣ Chekni screenshot qiling\n"
        "3️⃣ Screenshot ni shu yerga yuboring\n\n"
        "📸 <b>To'lov chekini screenshot qilib yuboring:</b>",
        parse_mode="HTML",
        reply_markup=kb_back_inline("pay:back_method")
    )
    await call.answer()


@router.message(PaymentFlow.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get("payment_id")
    plan_key = data.get("plan_key")
    plan = config.PLANS.get(plan_key, {})

    if not payment_id:
        await message.answer("❌ Sessiya muddati o'tdi. /start dan qayta boshlang.")
        await state.clear()
        return

    photo = message.photo[-1]
    file_id = photo.file_id
    await update_payment(payment_id, screenshot_file_id=file_id)

    await message.answer(
        "✅ <b>Screenshot qabul qilindi!</b>\n\n"
        "⏳ To'lovingiz admin tomonidan ko'rib chiqilmoqda.\n"
        "Tasdiqlangach, xabar olasiz.",
        parse_mode="HTML"
    )
    await state.clear()

    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id,
                photo=file_id,
                caption=(
                    f"📸 <b>To'lov Screenshoti Keldi</b>\n\n"
                    f"👤 Foydalanuvchi: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a>\n"
                    f"🆔 ID: <code>{message.from_user.id}</code>\n"
                    f"📋 Reja: <b>{plan.get('name', '')}</b>\n"
                    f"💰 Summa: <b>{plan.get('price', 0):,} so'm</b>\n"
                    f"🔖 To'lov ID: <code>{payment_id}</code>"
                ),
                parse_mode="HTML",
                reply_markup=kb_approve_payment(payment_id, message.from_user.id)
            )
        except Exception as e:
            logger.error(f"Admin xabarnoma xatosi: {e}")


@router.message(PaymentFlow.waiting_screenshot)
async def wrong_screenshot(message: Message):
    await message.answer("📸 Iltimos, to'lov chekining <b>rasmini</b> yuboring.", parse_mode="HTML")


# ─── Admin: Tasdiqlash / Rad etish ────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_pay:"))
async def cb_admin_payment_action(call: CallbackQuery):
    if call.from_user.id not in config.ADMIN_IDS:
        await call.answer("❌ Ruxsat yo'q.", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[1]
    payment_id = int(parts[2])
    user_tg_id = int(parts[3])

    payment = await get_payment(payment_id)
    if not payment:
        await call.answer("To'lov topilmadi.", show_alert=True)
        return

    if payment.status != "pending":
        await call.answer(f"Allaqachon: {payment.status}.", show_alert=True)
        return

    if action == "approve":
        await update_payment(payment_id, status="approved", processed_at=datetime.utcnow())
        await activate_subscription(payment.user_id, payment.plan_key, payment_id)
        plan = config.PLANS.get(payment.plan_key, {})

        try:
            user = await get_or_create_user(user_tg_id)
            await call.bot.send_message(
                user_tg_id,
                f"🎉 <b>To'lov Tasdiqlandi!</b>\n\n"
                f"✅ <b>{plan.get('name', '')}</b> obunangiz faollashtirildi!\n"
                f"📅 Muddat: <b>{plan.get('days', 0)} kun</b>\n\n"
                "Botdan to'liq foydalanishingiz mumkin.\n"
                "Asosiy menyuga o'tish uchun /menu yuboring.",
                parse_mode="HTML",
                reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
            )
        except Exception as e:
            logger.error(f"Foydalanuvchi {user_tg_id} ga xabar yuborishda xato: {e}")

        suffix = "\n\n✅ <b>TASDIQLANDI</b>"
        try:
            if call.message.caption:
                await call.message.edit_caption(call.message.caption + suffix, parse_mode="HTML")
            else:
                await call.message.edit_text(call.message.text + suffix, parse_mode="HTML")
        except Exception:
            pass
        await call.answer("✅ Tasdiqlandi!", show_alert=True)

    elif action == "reject":
        await update_payment(payment_id, status="rejected", processed_at=datetime.utcnow())

        try:
            await call.bot.send_message(
                user_tg_id,
                "❌ <b>To'lov Rad Etildi</b>\n\n"
                "To'lovingiz admin tomonidan rad etildi.\n\n"
                "Xato deb hisoblasangiz admin bilan bog'laning.\n"
                "Qayta urinish uchun /start yuboring.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Foydalanuvchi {user_tg_id} ga xabar yuborishda xato: {e}")

        suffix = "\n\n❌ <b>RAD ETILDI</b>"
        try:
            if call.message.caption:
                await call.message.edit_caption(call.message.caption + suffix, parse_mode="HTML")
            else:
                await call.message.edit_text(call.message.text + suffix, parse_mode="HTML")
        except Exception:
            pass
        await call.answer("❌ Rad etildi!", show_alert=True)
