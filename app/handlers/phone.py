import asyncio
import logging
import io
from html import escape
from contextlib import suppress
from telethon import errors
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.access import has_access
import re
import time
from datetime import datetime

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
    return has_access(user)


async def _disconnect_pending(user_tg_id: int):
    pending = _pending_logins.pop(user_tg_id, None)
    if not pending:
        return
    task = pending.get("task")
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    with suppress(Exception):
        await pending["client"].disconnect()
    if pending.get("qr_message"):
        with suppress(Exception):
            await pending["qr_message"].delete()


def _login_controls(resend=False):
    builder = InlineKeyboardBuilder()
    if resend:
        builder.button(text="🔄 Kodni qayta so'rash", callback_data="phone:resend")
    builder.button(text="📷 QR orqali ulash (kodsiz)", callback_data="phone:qr")
    builder.button(text="❌ Bekor qilish", callback_data="phone:cancel")
    builder.adjust(1)
    return builder.as_markup()


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
    from app.services.sender_service import stop_all_for_user
    await stop_all_for_user(user.id)

    try:
        await telethon_service.disconnect_client(user.id)
    except Exception as e:
        logger.warning(f"Disconnect error for {user.id}: {e}")

    await update_user(
        call.from_user.id,
        session_string=None,
        phone_number=None,
        proxy_index=None,
        proxy_key=None,
        device_model=None,
        device_system_version=None,
        device_app_version=None,
        session_connected_at=None,
    )
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
    user = await get_or_create_user(message.from_user.id)
    from app.services.sender_service import stop_all_for_user
    await stop_all_for_user(user.id)
    await telethon_service.disconnect_client(user.id)

    await message.answer(
        "⏳ <b>Tasdiqlash kodi yuborilmoqda...</b>\n\n<i>Iltimos kuting...</i>",
        parse_mode="HTML",
        reply_markup=remove_kb
    )

    try:
        client, result, proxy_key, device = await telethon_service.send_code(phone)
        _pending_logins[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
            "created_at": time.time(),
            "proxy_key": proxy_key,
            "device": device,
            "resend_at": time.monotonic() + max(30, result.timeout or 60),
            "can_resend": result.next_type is not None,
            "resend_used": False,
            "code_length": getattr(result.type, "length", 5),
        }
        await state.update_data(phone=phone)
        await state.set_state(PhoneFlow.entering_code)

        await message.answer(
            "📨 <b>Telegram javobi</b>\n\n" + telethon_service.code_delivery(result) +
            "\n\nKodni yozib yuboring. Kod kelmasa, pastdagi QR orqali ulashdan foydalaning.",
            parse_mode="HTML", reply_markup=_login_controls(result.next_type is not None))

    except telethon_service.DirectConnectionException as exc:
        from html import escape
        await message.answer(
            "❌ <b>Ulanish amalga oshmadi</b>\n\n" + escape(str(exc)),
            parse_mode="HTML", reply_markup=_login_controls())
        await state.set_state(PhoneFlow.entering_phone)

    except telethon_service.FloodWaitException as e:
        minutes = max(1, (e.seconds + 59) // 60)
        logger.warning(f"FloodWait {e.seconds}s — {phone} uchun")
        await message.answer(
            "⏳ <b>Telegram vaqtincha yangi kod so'rovlarini cheklab qo'ydi.</b>\n\n"
            f"Iltimos, taxminan <b>{minutes} daqiqadan</b> so'ng qayta urinib ko'ring.\n\n"
            "Telegram qaytargan kutish muddati tugagach qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=_login_controls()
        )
        await state.set_state(PhoneFlow.entering_phone)

    except Exception as e:
        logger.error(f"Kod yuborishda xato {phone}: {e}")
        await message.answer(
            f"❌ <b>Kod yuborib bo'lmadi</b>\n\n"
            f"Xato: <code>{escape(str(e)[:200])}</code>\n\n"
            "Raqamni tekshirib qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=_login_controls()
        )
        await state.set_state(PhoneFlow.entering_phone)


async def _finish_login(message, state, pending):
    account = await asyncio.wait_for(pending["client"].get_me(), timeout=20)
    if not account or getattr(account, "bot", False):
        raise telethon_service.DirectConnectionException("Shaxsiy Telegram hisobini ulang.")
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    device = pending.get("device") or telethon_service.DEVICE_PROFILE
    await update_user(message.from_user.id,
                      session_string=pending["client"].session.save(),
                      phone_number=getattr(account, "phone", None) or pending.get("phone"),
                      proxy_key=None, proxy_index=None, device_model=device[0],
                      device_system_version=device[1], device_app_version=device[2],
                      session_connected_at=datetime.utcnow())
    await _disconnect_pending(message.from_user.id)
    await state.clear()
    await message.answer("✅ <b>Telegram hisobingiz proksisiz ulandi!</b>",
                         reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=True))


@router.message(PhoneFlow.entering_code, F.text)
async def receive_code(message: Message, state: FSMContext):
    pending = _pending_logins.get(message.from_user.id)
    if not pending or pending.get("qr"):
        await message.answer("Kirish muddati o'tdi. Hisobni qayta ulang.", reply_markup=_login_controls())
        return
    code = re.sub(r"[\s.-]", "", message.text)
    if not code.isdigit() or len(code) != pending.get("code_length", 5):
        await message.answer("Kod formatini tekshiring va qayta kiriting.")
        return
    try:
        await telethon_service.sign_in(pending["client"], pending["phone"], code, pending["phone_code_hash"])
        await _finish_login(message, state, pending)
    except errors.SessionPasswordNeededError:
        await state.set_state(PhoneFlow.entering_password)
        await message.answer("🔐 Ikki bosqichli tasdiqlash yoqilgan. Bulut parolingizni kiriting:",
                             reply_markup=kb_cancel_inline("phone:cancel"))
    except errors.PhoneCodeInvalidError:
        await message.answer("❌ Kod noto'g'ri. Qayta kiriting.")
    except errors.PhoneCodeExpiredError:
        await _disconnect_pending(message.from_user.id)
        await state.clear()
        await message.answer("Kod muddati tugadi. Qayta ulang yoki QR orqali kiring.", reply_markup=_login_controls())
    except errors.FloodWaitError as exc:
        await message.answer(f"Telegram {exc.seconds} soniya kutishni so'radi.")
    except Exception:
        logger.exception("Account sign-in failed")
        await message.answer("Kirish bajarilmadi. Qayta urinib ko'ring yoki QR orqali ulang.", reply_markup=_login_controls())


@router.message(PhoneFlow.entering_password, F.text)
async def receive_2fa_password(message: Message, state: FSMContext):
    pending = _pending_logins.get(message.from_user.id)
    if not pending:
        await state.clear()
        await message.answer("Kirish muddati tugadi. Qayta ulang.", reply_markup=_login_controls())
        return
    try:
        await telethon_service.sign_in(pending["client"], pending.get("phone", ""), "", "", password=message.text)
        await _finish_login(message, state, pending)
    except errors.PasswordHashInvalidError:
        await message.answer("❌ Bulut paroli noto'g'ri. Qayta kiriting.")
    except errors.FloodWaitError as exc:
        await message.answer(f"Telegram {exc.seconds} soniya kutishni so'radi.")
    except Exception:
        logger.exception("2FA sign-in failed")
        await message.answer("Tasdiqlash amalga oshmadi. Qayta urinib ko'ring.")
    finally:
        with suppress(Exception):
            await message.delete()


@router.callback_query(PhoneFlow.entering_code, F.data == "phone:resend")
async def cb_resend_code(call: CallbackQuery, state: FSMContext):
    pending = _pending_logins.get(call.from_user.id)
    if not pending or pending.get("qr") or not pending.get("can_resend") or pending.get("resend_used"):
        await call.answer("Qayta so'rash mavjud emas. QR orqali ulashing.", show_alert=True)
        return
    remaining = int(pending["resend_at"] - time.monotonic())
    if remaining > 0:
        await call.answer(f"Yana {remaining + 1} soniya kuting.", show_alert=True)
        return
    pending["resend_used"] = True
    await call.answer("Telegramdan qayta so'ralmoqda...")
    try:
        result = await telethon_service.resend_code(pending["client"], pending["phone"], pending["phone_code_hash"])
        pending["phone_code_hash"] = result.phone_code_hash
        pending["code_length"] = getattr(result.type, "length", 5)
        await call.message.answer(telethon_service.code_delivery(result), reply_markup=_login_controls())
    except telethon_service.FloodWaitException as exc:
        await call.message.answer(f"Telegram {exc.seconds} soniya kutishni so'radi.", reply_markup=_login_controls())
    except Exception:
        await call.message.answer("Kodni qayta so'rab bo'lmadi. QR orqali ulang.", reply_markup=_login_controls())


def _qr_image(url):
    import qrcode
    from qrcode.image.pure import PyPNGImage
    output = io.BytesIO()
    qrcode.make(url, image_factory=PyPNGImage, box_size=6, border=4).save(output)
    return output.getvalue()


@router.callback_query(F.data == "phone:qr")
async def cb_qr_login(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    if not has_access(user):
        await call.answer("Faol obuna kerak.", show_alert=True)
        return
    await call.answer("QR tayyorlanmoqda...")
    await _disconnect_pending(call.from_user.id)
    from app.services.sender_service import stop_all_for_user
    await stop_all_for_user(user.id)
    await telethon_service.disconnect_client(user.id)
    await state.clear()
    client = None
    try:
        client = await telethon_service.connect_login_client()
        qr = await asyncio.wait_for(client.qr_login(), timeout=30)
        image = _qr_image(qr.url)
        qr_message = await call.message.answer_photo(BufferedInputFile(image, filename="telegram-login.png"),
            caption="📷 Telegram → Sozlamalar → Qurilmalar → Qurilma ulash orqali shu QR'ni skanerlang. "
                    "QR'ni boshqa ekranda oching. Kod kelishini kutish kerak emas. QR muddati qisqa; eskirsa yangisini oling.",
            reply_markup=_login_controls())
        pending = {"client": client, "qr": True, "created_at": time.time(),
                   "device": telethon_service.DEVICE_PROFILE, "qr_message": qr_message}
        _pending_logins[call.from_user.id] = pending
        await state.set_state(PhoneFlow.waiting_qr)
        pending["task"] = asyncio.create_task(_wait_qr(call, state, pending, qr))
    except Exception:
        if client:
            await telethon_service._safe_disconnect(client)
        logger.warning("QR preparation failed", exc_info=False)
        await call.message.answer("QR tayyorlab bo'lmadi. Qayta urinib ko'ring.", reply_markup=_login_controls())


async def _wait_qr(call, state, pending, qr):
    # Use the callback sender, not the bot-authored message's from_user.
    class Target:
        from_user = call.from_user
        async def answer(self, *args, **kwargs):
            return await call.message.answer(*args, **kwargs)
    try:
        await qr.wait()
        await _finish_login(Target(), state, pending)
    except errors.SessionPasswordNeededError:
        with suppress(Exception):
            await pending["qr_message"].delete()
        await state.set_state(PhoneFlow.entering_password)
        await call.message.answer("🔐 QR tasdiqlandi. Endi Telegram bulut parolingizni kiriting:",
                                  reply_markup=kb_cancel_inline("phone:cancel"))
    except asyncio.TimeoutError:
        await _disconnect_pending(call.from_user.id)
        await state.clear()
        await call.message.answer("QR muddati tugadi. Yangi QR oling.", reply_markup=_login_controls())
    except asyncio.CancelledError:
        raise
    except Exception:
        await _disconnect_pending(call.from_user.id)
        await state.clear()
        await call.message.answer("QR tasdiqlanmadi. Qayta urinib ko'ring.", reply_markup=_login_controls())


async def shutdown_logins():
    await asyncio.gather(*(_disconnect_pending(uid) for uid in list(_pending_logins)), return_exceptions=True)
