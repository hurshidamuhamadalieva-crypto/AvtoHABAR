import asyncio
import logging
import re
import time

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import get_or_create_user, update_user
from app.keyboards import (
    kb_phone_menu, kb_confirm_disconnect, kb_share_phone, kb_cancel_inline,
    kb_main_menu, remove_kb
)
from app.states import PhoneFlow
from app.services import telethon_service
from config import config

router = Router()
logger = logging.getLogger(__name__)

# user_tg_id -> {client, phone, phone_code_hash, created_at}
# MUHIM: "created_at" — foydalanuvchi jarayonni tashlab ketsa (bekor qilmasdan),
# bu yerda "osilib qolgan" Telethon ulanishi cheksiz ochiq turib qolmasligi
# uchun. Vaqti-vaqti bilan _cleanup_stale_logins() shularni tozalaydi.
_pending_logins = {}
PENDING_LOGIN_TTL = 600  # 10 daqiqa


def _check_active(user) -> bool:
    return user and user.is_active and not user.is_banned


async def _disconnect_pending(user_tg_id: int):
    pending = _pending_logins.pop(user_tg_id, None)
    if pending:
        try:
            await pending["client"].disconnect()
        except Exception:
            pass


async def cleanup_stale_logins():
    """
    Fon vazifasi: uzoq vaqt yakunlanmagan (bekor ham qilinmagan, tasdiqlanmagan
    ham) login urinishlarining Telethon ulanishini yopib, resurslarni bo'shatadi.
    Buni ishlatmasak, har bir yangi "Raqam qo'shish" urinishi ochiq qoladigan
    ulanishlar to'planib boradi va vaqt o'tishi bilan (masalan, Railway'da
    bir necha kundan keyin) serverning ulanish/resurs limiti tugab, yangi
    foydalanuvchilarga tasdiqlash kodi umuman kelmay qo'yishi mumkin edi.
    """
    while True:
        try:
            now = time.time()
            stale = [
                uid for uid, p in list(_pending_logins.items())
                if now - p.get("created_at", now) > PENDING_LOGIN_TTL
            ]
            for uid in stale:
                logger.info(f"Eskirgan login urinishi tozalanmoqda: {uid}")
                await _disconnect_pending(uid)
        except Exception as e:
            logger.error(f"cleanup_stale_logins xatosi: {e}")
        await asyncio.sleep(120)


async def _show_phone_menu(target, user, edit: bool = False):
    has_phone = bool(user.session_string)
    if has_phone:
        phone_text = user.phone_number if user.phone_number else "Noma'lum"
        text = (
            f"📱 <b>Telegram Hisob Boshqaruvi</b>\n\n"
            f"✅ Ulangan raqam: <code>{phone_text}</code>\n\n"
            "Nima qilishni xohlaysiz?"
        )
    else:
        text = (
            "📱 <b>Telegram Hisobingizni Ulash</b>\n\n"
            "⚠️ <b>Muhim:</b>\n"
            "Bot sizning Telegram hisobingizga ulanib, guruhlarga xabar yuboradi.\n\n"
            "🔐 <b>Xavfsizlik:</b>\n"
            "• Sessiyangiz serverda xavfsiz saqlanadi\n"
            "• Shaxsiy xabarlaringizga hech qachon kirilmaydi\n"
            "• Istalgan vaqt uzishingiz mumkin\n"
            "• Xabarlar faqat siz tanlagan guruhlarga yuboriladi\n\n"
            "📋 <b>Rozilik shartlari:</b>\n"
            "• Hisobingiz e'lonlarni yuborish uchun ishlatiladi\n"
            "• Yuborilgan kontent uchun o'zingiz javobgarsiz\n"
            "• Ommaviy xabar yuborish Telegram shartlariga mos bo'lishi kerak\n\n"
            "Davom etishga rozimisiz?"
        )
    markup = kb_phone_menu(has_phone=has_phone)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "menu:phone")
async def cb_open_phone_menu(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    if not _check_active(user):
        await call.answer("❌ Bu funksiyadan foydalanish uchun faol obuna kerak.", show_alert=True)
        return
    await state.set_state(PhoneFlow.agreeing)
    await _show_phone_menu(call.message, user, edit=True)
    await call.answer()


# ─── Disconnect / O'chirish ───────────────────────────────────────────────────

@router.callback_query(F.data == "phone:disconnect")
async def cb_phone_disconnect(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "🗑 <b>Raqamni uzish</b>\n\n"
        "⚠️ Hisobingiz uziladi va session o'chiriladi.\n"
        "Yuborishlar to'xtaydi.\n\n"
        "Davom etishni xohlaysizmi?",
        parse_mode="HTML",
        reply_markup=kb_confirm_disconnect()
    )
    await call.answer()


@router.callback_query(F.data == "phone:confirm_disconnect")
async def cb_confirm_disconnect(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)

    try:
        await telethon_service.disconnect_client(user.id)
    except Exception as e:
        logger.warning(f"Disconnect error for {user.id}: {e}")

    await update_user(call.from_user.id, session_string=None, phone_number=None)
    await state.clear()

    await call.message.edit_text(
        "✅ <b>Raqam muvaffaqiyatli uzildi!</b>\n\n"
        "📱 Session o'chirildi.\n"
        "Yangi raqam ulash uchun <b>📱 Raqam qo'shish</b> tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=False)
    )
    await call.answer("✅ Uzildi!")


# ─── Agree & Enter Phone ──────────────────────────────────────────────────────

@router.callback_query(F.data == "phone:agree")
async def cb_phone_agree(call: CallbackQuery, state: FSMContext):
    await state.set_state(PhoneFlow.entering_phone)
    await call.message.edit_text(
        "📱 <b>Telefon raqamingizni kiriting</b>\n\n"
        "Xalqaro formatda yuboring:\n"
        "<code>+998901234567</code>\n\n"
        "Yoki pastdagi tugma orqali kontaktingizni ulashing:",
        parse_mode="HTML"
    )
    await call.message.answer("👇 Kontakt ulashing yoki raqam kiriting:", reply_markup=kb_share_phone())
    await call.answer()


@router.callback_query(F.data == "phone:cancel")
async def cb_phone_cancel(call: CallbackQuery, state: FSMContext):
    await _disconnect_pending(call.from_user.id)
    await state.clear()
    user = await get_or_create_user(call.from_user.id)
    try:
        await call.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        pass
    await call.message.answer(
        "📋 Asosiy menyu:",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    )
    await call.answer()


@router.message(PhoneFlow.entering_phone, F.text == "✏️ Qo'lda kiritish")
async def hint_manual_phone(message: Message, state: FSMContext):
    await message.answer(
        "✏️ Raqamingizni xalqaro formatda yozib yuboring:\n<code>+998901234567</code>",
        parse_mode="HTML"
    )


@router.message(PhoneFlow.entering_phone, F.contact)
async def receive_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await _process_phone(message, state, phone)


@router.message(PhoneFlow.entering_phone, F.text)
async def receive_phone_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Bekor qilish":
        await state.clear()
        user = await get_or_create_user(message.from_user.id)
        await message.answer("❌ Bekor qilindi.", reply_markup=remove_kb)
        await message.answer(
            "📋 Asosiy menyu:",
            reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
        )
        return

    phone = re.sub(r"[^\d+]", "", text)
    if not phone.startswith("+"):
        phone = "+" + phone

    if len(phone) < 10:
        await message.answer(
            "❌ Noto'g'ri raqam. Xalqaro formatda kiriting:\n<code>+998901234567</code>",
            parse_mode="HTML"
        )
        return

    await _process_phone(message, state, phone)


async def _process_phone(message: Message, state: FSMContext, phone: str):
    # Agar shu foydalanuvchi uchun avval ochilib, yakunlanmagan urinish bo'lsa —
    # yangisini boshlashdan oldin uni tozalaymiz (ulanishlar to'planib qolmasin).
    await _disconnect_pending(message.from_user.id)

    await message.answer(
        "⏳ <b>Tasdiqlash kodi yuborilmoqda...</b>\n\n<i>Iltimos kuting...</i>",
        parse_mode="HTML",
        reply_markup=remove_kb
    )

    try:
        client, result = await telethon_service.send_code(phone)
        _pending_logins[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
            "created_at": time.time(),
        }
        await state.update_data(phone=phone)
        await state.set_state(PhoneFlow.entering_code)

        await message.answer(
            "✅ <b>Tasdiqlash kodi yuborildi!</b>\n\n"
            f"📱 Raqam: <code>{phone}</code>\n\n"
            "📝 Kodni quyidagi formatda kiriting:\n"
            "<b>1 2 . 3 4 5</b>  →  <code>12.345</code>\n\n"
            "⚠️ Nuqta birinchi 2 va oxirgi 3 raqamni ajratadi.\n\n"
            "🔒 <i>Bu kodni hech kimga bermang!</i>",
            parse_mode="HTML",
            reply_markup=kb_cancel_inline("phone:cancel")
        )

    except Exception as e:
        logger.error(f"Kod yuborishda xato {phone}: {e}")
        await message.answer(
            f"❌ <b>Kod yuborib bo'lmadi</b>\n\n"
            f"Xato: <code>{str(e)[:200]}</code>\n\n"
            "Raqamni tekshirib qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=kb_cancel_inline("phone:cancel")
        )
        await state.set_state(PhoneFlow.entering_phone)


@router.message(PhoneFlow.entering_code, F.text)
async def receive_code(message: Message, state: FSMContext):
    text = message.text.strip()

    code = text.replace(".", "").replace(" ", "").strip()
    if not code.isdigit() or len(code) < 4:
        await message.answer(
            "❌ Noto'g'ri format. Kodni shunday kiriting:\n<code>12.345</code>",
            parse_mode="HTML",
            reply_markup=kb_cancel_inline("phone:cancel")
        )
        return

    pending = _pending_logins.get(message.from_user.id)
    if not pending:
        await message.answer("❌ Sessiya muddati o'tdi. /start dan qayta boshlang.")
        await state.clear()
        return

    loading_msg = await message.answer("⏳ <b>Kod tekshirilmoqda...</b>", parse_mode="HTML")

    try:
        session_string = await telethon_service.sign_in(
            client=pending["client"],
            phone=pending["phone"],
            code=code,
            phone_code_hash=pending["phone_code_hash"],
        )

        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        await update_user(message.from_user.id, session_string=session_string, phone_number=pending["phone"])

        # Muvaqqat login klientini yopamiz — doimiy ulanish keyinchalik
        # telethon_service.get_client() orqali alohida, boshqariladigan pool'da
        # qayta ochiladi. Buni yopmasak, har bir muvaffaqiyatli ulanishdan keyin
        # ham ortiqcha, boshqarilmaydigan bitta ulanish serverda "osilib" qolar
        # va vaqt o'tishi bilan (bir necha soat/kundan keyin) resurs yetishmay,
        # YANGI foydalanuvchilarga tasdiqlash kodi kelmay qolishiga sabab bo'lardi.
        try:
            await pending["client"].disconnect()
        except Exception:
            pass
        _pending_logins.pop(message.from_user.id, None)
        await state.clear()

        await loading_msg.edit_text(
            "✅ <b>Hisob muvaffaqiyatli ulandi!</b>\n\n"
            f"📱 Raqam: <code>{pending['phone']}</code>\n\n"
            "Telegram hisobingiz botga ulandi.\n"
            "Endi guruhlar qo'shib, e'lon yuborishni boshlashingiz mumkin!",
            parse_mode="HTML"
        )
        await message.answer(
            "📋 Asosiy menyuga qaytish:",
            reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=True)
        )

    except Exception as e:
        err_str = str(e)
        if "SessionPasswordNeeded" in err_str or "password" in err_str.lower():
            await state.set_state(PhoneFlow.entering_password)
            await state.update_data(code=code)
            await loading_msg.edit_text(
                "🔐 <b>Ikki bosqichli tasdiqlash</b>\n\n"
                "Hisobingizda 2FA yoqilgan.\n"
                "<b>Bulut parolingizni</b> kiriting:",
                parse_mode="HTML",
                reply_markup=kb_cancel_inline("phone:cancel")
            )
        elif "PhoneCodeInvalid" in err_str:
            await loading_msg.edit_text(
                "❌ <b>Noto'g'ri kod!</b>\n\nTo'g'ri kodni kiriting:",
                parse_mode="HTML",
                reply_markup=kb_cancel_inline("phone:cancel")
            )
        elif "PhoneCodeExpired" in err_str:
            await loading_msg.edit_text(
                "❌ <b>Kod muddati o'tdi!</b>\n\n/start dan qayta boshlang.",
                parse_mode="HTML"
            )
            await _disconnect_pending(message.from_user.id)
            await state.clear()
        else:
            logger.error(f"Kirish xatosi {message.from_user.id}: {e}")
            await loading_msg.edit_text(
                f"❌ <b>Xato:</b> <code>{err_str[:200]}</code>\n\nQayta urinib ko'ring.",
                parse_mode="HTML",
                reply_markup=kb_cancel_inline("phone:cancel")
            )


@router.message(PhoneFlow.entering_password, F.text)
async def receive_2fa_password(message: Message, state: FSMContext):
    pending = _pending_logins.get(message.from_user.id)
    if not pending:
        await message.answer("❌ Sessiya muddati o'tdi. Qayta boshlang.")
        await state.clear()
        return

    text = message.text.strip()
    data = await state.get_data()
    loading_msg = await message.answer("⏳ <b>Parol tekshirilmoqda...</b>", parse_mode="HTML")

    try:
        session_string = await telethon_service.sign_in(
            client=pending["client"],
            phone=pending["phone"],
            code=data.get("code", ""),
            phone_code_hash=pending["phone_code_hash"],
            password=text,
        )

        user = await get_or_create_user(message.from_user.id)
        await update_user(message.from_user.id, session_string=session_string, phone_number=pending["phone"])

        try:
            await pending["client"].disconnect()
        except Exception:
            pass
        _pending_logins.pop(message.from_user.id, None)
        await state.clear()

        await loading_msg.edit_text(
            "✅ <b>Hisob ulandi!</b>\n\n"
            "Ikki bosqichli tasdiqlash muvaffaqiyatli o'tdi.\n"
            "Hisobingiz ulandi!",
            parse_mode="HTML"
        )
        await message.answer(
            "📋 Asosiy menyu:",
            reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=True)
        )

    except Exception as e:
        logger.error(f"2FA xatosi {message.from_user.id}: {e}")
        await loading_msg.edit_text(
            "❌ <b>Noto'g'ri parol!</b>\n\nQayta kiriting:",
            parse_mode="HTML",
            reply_markup=kb_cancel_inline("phone:cancel")
        )
