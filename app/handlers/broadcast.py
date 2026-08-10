"""
E'lon yuborish — birlashtirilgan bo'lim.

Avval bu funksiya 3 ta alohida asosiy menyu tugmasiga bo'lingan edi:
"📝 E'lonlar" (matn kiritish), "⏱ Interval" (davriylikni tanlash) va
"▶️ Yuborishni boshlash". Foydalanuvchi uchun soddaroq bo'lishi uchun
булар ikkinchi botdagi "E'lon berish" bo'limi uslubida (matn -> interval ->
tasdiqlash -> avtomatik yuborish) BITTA ketma-ket oqimga birlashtirildi.

Yuborishning o'zi avvalgidek — foydalanuvchi ulagan Telegram hisobi
(session) orqali, u o'zi tanlagan guruhlarga — ishlaydi (app.services.sender_service).
"""
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import (
    get_or_create_user, get_or_create_settings, update_settings,
    get_active_advertisement, save_advertisement, get_user_groups
)
from app.keyboards import (
    kb_interval, kb_broadcast_confirm, kb_broadcast_menu, kb_broadcast_sending,
    kb_cancel_inline, kb_back_inline, kb_main_menu
)
from app.states import BroadcastFlow
from app.services import sender_service
from app.services.sender_service import DELAY_BETWEEN_GROUPS

router = Router()
logger = logging.getLogger(__name__)


def _check_active(user) -> bool:
    return user and user.is_active and not user.is_banned


async def _render_broadcast_home(message_target, user, edit: bool = True):
    """Bo'lim ochilganda yoki bekor qilinganda ko'rsatiladigan asosiy holat."""
    if not user.session_string:
        text = (
            "❌ <b>Hisob ulanmagan!</b>\n\n"
            "Avval <b>📱 Raqam qo'shish</b> orqali Telegram hisobingizni ulang."
        )
        markup = kb_back_inline("menu:home")
        return await _send(message_target, text, markup, edit)

    groups = await get_user_groups(user.id)
    if not groups:
        text = (
            "❌ <b>Guruhlar tanlanmagan!</b>\n\n"
            "<b>📂 Guruh qo'shish</b> orqali avval guruhlarni qo'shing."
        )
        markup = kb_back_inline("menu:home")
        return await _send(message_target, text, markup, edit)

    settings = await get_or_create_settings(user.id)
    ad = await get_active_advertisement(user.id)
    is_sending = sender_service.is_sending(user.id)

    if is_sending:
        preview = (ad.text[:200] + ("..." if ad and len(ad.text) > 200 else "")) if ad else ""
        text = (
            "📢 <b>E'lon Yuborish</b>\n\n"
            f"🚀 Holat: <b>Faol — yuborilmoqda</b>{' (⏸ pauzada)' if settings.sending_paused else ''}\n"
            f"👥 Guruhlar: <b>{len(groups)} ta</b>\n"
            f"⏱ Interval: <b>{settings.interval_minutes} daqiqa</b>\n"
            f"📊 Jami yuborilgan: <b>{settings.messages_sent} ta</b>\n\n"
            f"📄 <b>Joriy e'lon:</b>\n<i>{preview}</i>"
        )
        markup = kb_broadcast_sending(is_paused=settings.sending_paused)
    else:
        preview = (
            f"\n\n📄 <b>Oxirgi e'lon:</b>\n<i>{ad.text[:200]}{'...' if len(ad.text) > 200 else ''}</i>"
            if ad else "\n\n📭 <i>Hali e'lon yuborilmagan.</i>"
        )
        text = (
            "📢 <b>E'lon Yuborish</b>\n\n"
            f"👥 Guruhlar: <b>{len(groups)} ta</b> tayyor\n"
            f"⏱ Interval: <b>{settings.interval_minutes} daqiqa</b>"
            + preview
        )
        markup = kb_broadcast_menu(is_sending=False, has_ad=bool(ad))

    await _send(message_target, text, markup, edit)


async def _send(message_target, text, markup, edit):
    if edit:
        try:
            await message_target.edit_text(text, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    await message_target.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "menu:broadcast")
async def cb_open_broadcast(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    if not _check_active(user):
        await call.answer("❌ Bu funksiyadan foydalanish uchun faol obuna kerak.", show_alert=True)
        return
    await state.clear()
    await _render_broadcast_home(call.message, user, edit=True)
    await call.answer()


async def _prompt_ad_text(call: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastFlow.waiting_text)
    await call.message.edit_text(
        "✏️ <b>E'lon matnini yuboring</b>\n\n"
        "Barcha formatlash qo'llab-quvvatlanadi (qalin, kursiv, havolalar).\n\n"
        "💡 <i>Maslahat: Qisqa va qiziqarli matn yozing!</i>",
        parse_mode="HTML",
        reply_markup=kb_cancel_inline("bc_cancel")
    )
    await call.answer()


@router.callback_query(F.data == "bc_new")
async def cb_bc_new(call: CallbackQuery, state: FSMContext):
    await _prompt_ad_text(call, state)


@router.callback_query(F.data == "bc_continue")
async def cb_bc_continue(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    ad = await get_active_advertisement(user.id)
    if not ad:
        await _prompt_ad_text(call, state)
        return
    await state.update_data(ad_text=ad.text)
    await state.set_state(BroadcastFlow.choosing_interval)
    await call.message.edit_text(
        f"♻️ Oxirgi e'loningiz asosida davom etyapmiz (matn qayta so'ralmaydi).\n\n"
        f"✍️ Matn: <i>{ad.text[:200]}{'...' if len(ad.text) > 200 else ''}</i>\n\n"
        "⏱ E'lon necha daqiqada bir marta yuborilsin?",
        parse_mode="HTML",
        reply_markup=kb_interval()
    )
    await call.answer()


@router.message(BroadcastFlow.waiting_text, F.text)
async def receive_ad_text(message: Message, state: FSMContext):
    await state.update_data(ad_text=message.text)
    await state.set_state(BroadcastFlow.choosing_interval)
    await message.answer(
        f"⏱ E'lon necha daqiqada bir marta yuborilsin?\n\n✍️ Matn: {message.text[:200]}",
        reply_markup=kb_interval()
    )


@router.callback_query(BroadcastFlow.choosing_interval, F.data.startswith("bc_interval:"))
async def cb_choose_interval(call: CallbackQuery, state: FSMContext):
    minutes = int(call.data.split(":")[1])
    await state.update_data(interval=minutes)
    data = await state.get_data()
    await state.set_state(BroadcastFlow.confirming)

    text_preview = (data.get("ad_text") or "")[:300]
    summary = (
        f"📋 <b>E'lon Xulosasi</b>\n\n"
        f"✍️ Matn: <i>{text_preview}</i>\n"
        f"⏱ Interval: <b>{minutes} daqiqa</b>\n\n"
        "Tasdiqlaysizmi? Tasdiqlasangiz e'lon <b>darhol</b> barcha guruhlaringizga yuboriladi, "
        f"so'ng har <b>{minutes} daqiqada</b> avtomatik qayta yuborilib turadi."
    )
    await call.message.edit_text(summary, parse_mode="HTML", reply_markup=kb_broadcast_confirm())
    await call.answer()


@router.callback_query(F.data == "bc_confirm")
async def cb_confirm_broadcast(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ad_text = data.get("ad_text")
    interval = data.get("interval")

    if not ad_text or not interval:
        await state.clear()
        await call.answer("Xatolik: ma'lumotlar topilmadi. Qaytadan boshlang.", show_alert=True)
        return

    user = await get_or_create_user(call.from_user.id)
    groups = await get_user_groups(user.id)
    if not groups:
        await state.clear()
        await call.message.edit_text(
            "❌ <b>Guruhlar tanlanmagan!</b>\n\n"
            "<b>📂 Guruh qo'shish</b> orqali avval guruhlarni qo'shing.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return

    await save_advertisement(user.id, ad_text)
    await update_settings(user.id, interval_minutes=interval)
    await state.set_state(BroadcastFlow.active)

    await sender_service.start_sending(call.bot, user.id, call.from_user.id, user.session_string)

    estimated_cycle = (len(groups) * DELAY_BETWEEN_GROUPS) // 60

    await call.message.edit_text(
        f"🚀 <b>Yuborish boshlandi!</b>\n\n"
        f"📝 E'lon: ✅\n"
        f"👥 Guruhlar: <b>{len(groups)} ta</b>\n"
        f"⏱ Guruhlar orasidagi pauza: <b>{DELAY_BETWEEN_GROUPS} soniya</b>\n"
        f"🔄 Bir sikl taxminan: <b>~{max(1, estimated_cycle)} daqiqa</b>\n"
        f"⏳ Sikl orasidagi interval: <b>{interval} daqiqa</b>\n\n"
        "✅ Xabarlar avtomatik yuborilmoqda.",
        parse_mode="HTML",
        reply_markup=kb_broadcast_sending(is_paused=False)
    )
    await call.answer("🚀 Yuborish boshlandi!")


@router.callback_query(F.data == "bc_cancel")
async def cb_cancel_broadcast(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(call.from_user.id)
    await _render_broadcast_home(call.message, user, edit=True)
    await call.answer("❌ Bekor qilindi.")


@router.callback_query(F.data == "bc_pause")
async def cb_pause_sending(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    await sender_service.pause_sending(user.id)
    await call.message.edit_reply_markup(reply_markup=kb_broadcast_sending(is_paused=True))
    await call.answer("⏸ Yuborish to'xtatib turildi.")


@router.callback_query(F.data == "bc_resume")
async def cb_resume_sending(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    await sender_service.resume_sending(user.id)
    await call.message.edit_reply_markup(reply_markup=kb_broadcast_sending(is_paused=False))
    await call.answer("▶️ Yuborish davom ettirildi!")


@router.callback_query(F.data == "bc_stop")
async def cb_stop_sending(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    await sender_service.stop_sending(user.id)
    await state.clear()

    settings = await get_or_create_settings(user.id)
    await call.message.edit_text(
        f"⏹ <b>Yuborish to'xtatildi</b>\n\n"
        f"📊 Jami yuborilgan xabarlar: <b>{settings.messages_sent} ta</b>\n\n"
        "AutoAd Bot dan foydalanganingiz uchun rahmat!",
        parse_mode="HTML",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    )
    await call.answer("⏹ To'xtatildi.")
