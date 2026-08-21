import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.database import get_or_create_user, get_user_groups, save_groups
from app.keyboards import (
    kb_main_menu, kb_back_inline, kb_groups_menu, kb_confirm_clear_groups,
    kb_folders, kb_bot_folder_actions, kb_group_picker,
)
from app.states import GroupFlow
from app.services import telethon_service
from app.services.telethon_service import BOT_FOLDER_TITLE
from config import config

router = Router()
logger = logging.getLogger(__name__)

_user_folders = {}  # temp cache: user_tg_id -> [folder dicts]

GROUPS_PER_PAGE = 10

# guruh-tanlash ekrani uchun vaqtinchalik keshlar (foydalanuvchi Telegram ID bo'yicha)
_picker_all_groups = {}   # user_tg_id -> [group dicts]
_picker_selected_ids = {}  # user_tg_id -> set(abs(group_id))


def _check_active(user) -> bool:
    return user and user.is_active and not user.is_banned


def _warmup_remaining_seconds(user) -> int:
    """
    Akkaunt yaqinda ulangan bo'lsa, "isinish" davri tugashiga qancha
    qolganini (soniyada) qaytaradi. Agar isinish davri allaqachon
    tugagan bo'lsa yoki ulanish vaqti noma'lum bo'lsa (juda eski
    akkauntlar) — 0 qaytaradi.
    """
    if not user.session_connected_at:
        return 0
    elapsed = datetime.utcnow() - user.session_connected_at
    remaining = timedelta(minutes=config.WARMUP_MINUTES) - elapsed
    return max(0, int(remaining.total_seconds()))


@router.callback_query(F.data == "menu:groups")
async def cb_open_groups_menu(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    if not _check_active(user):
        await call.answer("❌ Bu funksiyadan foydalanish uchun faol obuna kerak.", show_alert=True)
        return

    if not user.session_string:
        await call.message.edit_text(
            "❌ <b>Hisob ulanmagan!</b>\n\n"
            "Avval <b>📱 Raqam qo'shish</b> orqali Telegram hisobingizni ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return

    # Mavjud guruhlarni ko'rsatish
    existing_groups = await get_user_groups(user.id)
    if existing_groups:
        folder_name = existing_groups[0].folder_name or "Noma'lum jild"
        group_list = "\n".join(f"  • {g.group_title}" for g in existing_groups[:8])
        if len(existing_groups) > 8:
            group_list += f"\n  ... va yana {len(existing_groups) - 8} ta"

        await call.message.edit_text(
            f"📂 <b>Guruhlar boshqaruvi</b>\n\n"
            f"📁 Joriy jild: <b>{folder_name}</b>\n"
            f"👥 Guruhlar soni: <b>{len(existing_groups)}</b>\n\n"
            f"<b>Guruhlar:</b>\n{group_list}\n\n"
            "Nima qilishni xohlaysiz?",
            parse_mode="HTML",
            reply_markup=kb_groups_menu()
        )
    else:
        await call.message.edit_text(
            "📂 <b>Guruhlar boshqaruvi</b>\n\n"
            "📭 Hali guruhlar qo'shilmagan.\n\n"
            "Jild tanlash orqali guruhlarni import qiling:",
            parse_mode="HTML",
            reply_markup=kb_groups_menu()
        )
    await call.answer()


# ─── "Bot uchun" jildini yaratish/yangilash (avtomatik, bo'sh) ────────────────

@router.callback_query(F.data == "groups:bot_folder")
async def cb_bot_folder(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)

    # Guruh-tanlash ekranidan eski (boshqa jild uchun) kesh qolib
    # ketmasligi uchun tozalab qo'yamiz.
    _picker_all_groups.pop(call.from_user.id, None)
    _picker_selected_ids.pop(call.from_user.id, None)

    remaining = _warmup_remaining_seconds(user)
    if remaining > 0:
        minutes = remaining // 60 + (1 if remaining % 60 else 0)
        await call.message.edit_text(
            "⏳ <b>Hisobingiz hali \"isinib\" ulgurmadi</b>\n\n"
            "Xavfsizlik uchun yangi ulangan hisobda guruhlarni darhol "
            "olib bo'lmaydi — Telegram buni shubhali harakat deb "
            "hisoblab, hisobingizni chiqarib yuborishi mumkin edi.\n\n"
            f"⏱ Taxminan <b>{minutes} daqiqadan</b> so'ng qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return

    loading = await call.message.edit_text(
        "⏳ <b>'Bot uchun' jildi yaratilmoqda...</b>", parse_mode="HTML"
    )

    try:
        groups, created = await telethon_service.get_or_create_bot_folder(user.id, user.session_string)

        if not groups:
            note = (
                "✅ <b>'Bot uchun' jildi yaratildi!</b>\n\n"
                if created else
                "📁 <b>'Bot uchun' jildi topildi, lekin hozircha bo'sh.</b>\n\n"
            )
            await loading.edit_text(
                note +
                "📲 Endi kerakli guruh(lar)ni <b>hohlasangiz qo'lda</b> "
                "(Telegram ilovangizda 'Bot uchun' jildiga qo'shib), "
                "<b>hohlasangiz pastdagi tugma orqali</b> qo'shib chiqing:",
                parse_mode="HTML",
                reply_markup=kb_bot_folder_actions()
            )
            await call.answer()
            return

        await save_groups(user.id, groups, BOT_FOLDER_TITLE)

        group_list = "\n".join(f"  • {g['title']}" for g in groups[:10])
        if len(groups) > 10:
            group_list += f"\n  ... va yana {len(groups) - 10} ta"

        await loading.edit_text(
            "✅ <b>'Bot uchun' jildidan guruhlar olindi!</b>\n\n"
            f"👥 Guruhlar: <b>{len(groups)}</b>\n\n"
            f"<b>Guruhlar:</b>\n{group_list}\n\n"
            "💡 <i>Yangi guruh qo'shmoqchi bo'lsangiz, uni Telegram "
            "ilovangizda 'Bot uchun' jildiga qo'shing yoki pastdagi tugma "
            "orqali qo'shing, so'ng shu tugmani qayta bosing.</i>",
            parse_mode="HTML",
            reply_markup=kb_bot_folder_actions()
        )

    except (telethon_service.SessionRevokedException, telethon_service.AccountBannedException) as e:
        banned = isinstance(e, telethon_service.AccountBannedException)
        await telethon_service.handle_lost_session(call.bot, user.id, call.from_user.id, banned=banned)
        await loading.edit_text(
            "🚫 <b>Akkauntingiz bloklangan.</b>\n\nBoshqa raqam bilan urinib ko'ring."
            if banned else
            "⚠️ <b>Hisobingiz uzilib qoldi!</b>\n\n"
            "Telegram hisobingiz uzilgani aniqlandi va bazadan tozalandi.\n"
            "Iltimos, <b>📱 Raqam qo'shish</b> orqali qayta ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return
    except Exception as e:
        logger.error(f"'Bot uchun' jildida xato {call.from_user.id}: {e}")
        await loading.edit_text(
            f"❌ <b>Xato yuz berdi.</b>\n\n"
            f"<code>{str(e)[:200]}</code>\n\n"
            "Hisobingizni qayta ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("groups:back")
        )
    await call.answer()


# ─── Guruhlarni bot ichida, tugma orqali tanlash (sahifalab) ──────────────────

async def _render_picker_page(target_message, user_tg_id: int, page: int, edit: bool = True):
    all_groups = _picker_all_groups.get(user_tg_id, [])
    selected_ids = _picker_selected_ids.get(user_tg_id, set())

    total_pages = max(1, (len(all_groups) + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * GROUPS_PER_PAGE
    page_groups = all_groups[start:start + GROUPS_PER_PAGE]

    text = (
        f"➕ <b>Guruhlarni tanlang</b>\n\n"
        f"Bosilgan guruh ✅ (tanlangan) bo'lib qoladi, qayta bossangiz "
        f"➕ (olib tashlangan) holatga qaytadi.\n\n"
        f"📄 Sahifa: <b>{page + 1}/{total_pages}</b> · Jami guruhlar: <b>{len(all_groups)}</b> · "
        f"Tanlangan: <b>{len(selected_ids)}</b>"
    )
    markup = kb_group_picker(page_groups, selected_ids, page, total_pages)

    if edit:
        try:
            await target_message.edit_text(text, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    await target_message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("gp_open:"))
async def cb_group_picker_open(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    page = int(call.data.split(":")[1])

    remaining = _warmup_remaining_seconds(user)
    if remaining > 0:
        minutes = remaining // 60 + (1 if remaining % 60 else 0)
        await call.message.edit_text(
            "⏳ <b>Hisobingiz hali \"isinib\" ulgurmadi</b>\n\n"
            f"⏱ Taxminan <b>{minutes} daqiqadan</b> so'ng qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return

    # Ro'yxat allaqachon keshda bo'lsa (sahifalar orasida o'tish), qayta
    # so'ramaymiz — bu tez ishlashi uchun. Faqat birinchi ochilishda
    # Telegram'dan so'raymiz.
    if call.from_user.id not in _picker_all_groups:
        loading = await call.message.edit_text("⏳ <b>Guruhlar yuklanmoqda...</b>", parse_mode="HTML")
        try:
            all_groups, selected_ids = await telethon_service.list_all_groups_with_selection(
                user.id, user.session_string
            )
        except (telethon_service.SessionRevokedException, telethon_service.AccountBannedException) as e:
            banned = isinstance(e, telethon_service.AccountBannedException)
            await telethon_service.handle_lost_session(call.bot, user.id, call.from_user.id, banned=banned)
            await loading.edit_text(
                "🚫 <b>Akkauntingiz bloklangan.</b>\n\nBoshqa raqam bilan urinib ko'ring."
                if banned else
                "⚠️ <b>Hisobingiz uzilib qoldi!</b>\n\nIltimos, qayta ulang.",
                parse_mode="HTML",
                reply_markup=kb_back_inline("menu:home")
            )
            await call.answer()
            return
        except Exception as e:
            logger.error(f"Guruhlarni yuklashda xato {call.from_user.id}: {e}")
            await loading.edit_text(
                f"❌ <b>Xato:</b> <code>{str(e)[:200]}</code>",
                parse_mode="HTML",
                reply_markup=kb_back_inline("groups:bot_folder")
            )
            await call.answer()
            return

        if not all_groups:
            await loading.edit_text(
                "❌ <b>Hisobingizda guruh topilmadi.</b>",
                parse_mode="HTML",
                reply_markup=kb_back_inline("groups:bot_folder")
            )
            await call.answer()
            return

        _picker_all_groups[call.from_user.id] = all_groups
        _picker_selected_ids[call.from_user.id] = selected_ids

    await _render_picker_page(call.message, call.from_user.id, page, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("gp:"))
async def cb_group_picker_toggle(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    _, page_str, group_id_str = call.data.split(":")
    page = int(page_str)
    group_id = int(group_id_str)

    selected_ids = _picker_selected_ids.setdefault(call.from_user.id, set())
    target_id = abs(group_id)
    is_currently_selected = target_id in selected_ids

    add = not is_currently_selected

    try:
        ok = await telethon_service.toggle_group_in_bot_folder(user.id, user.session_string, group_id, add=add)
    except (telethon_service.SessionRevokedException, telethon_service.AccountBannedException) as e:
        banned = isinstance(e, telethon_service.AccountBannedException)
        await telethon_service.handle_lost_session(call.bot, user.id, call.from_user.id, banned=banned)
        await call.message.edit_text(
            "🚫 <b>Akkauntingiz bloklangan.</b>" if banned else
            "⚠️ <b>Hisobingiz uzilib qoldi! Iltimos, qayta ulang.</b>",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return
    except Exception as e:
        logger.error(f"Guruh toggle xatosi: {e}")
        await call.answer("❌ Xato yuz berdi, qayta urinib ko'ring.", show_alert=True)
        return

    if not ok:
        await call.answer("❌ Xato yuz berdi, qayta urinib ko'ring.", show_alert=True)
        return

    if add:
        selected_ids.add(target_id)
        await call.answer("✅ Guruh qo'shildi!")
    else:
        selected_ids.discard(target_id)
        await call.answer("➖ Guruh olib tashlandi.")

    await _render_picker_page(call.message, call.from_user.id, page, edit=True)


@router.callback_query(F.data == "gp_done")
async def cb_group_picker_done(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    all_groups = _picker_all_groups.get(call.from_user.id, [])
    selected_ids = _picker_selected_ids.get(call.from_user.id, set())

    final_groups = [g for g in all_groups if abs(g["id"]) in selected_ids]

    if final_groups:
        await save_groups(user.id, final_groups, BOT_FOLDER_TITLE)

    _picker_all_groups.pop(call.from_user.id, None)
    _picker_selected_ids.pop(call.from_user.id, None)

    group_list = "\n".join(f"  • {g['title']}" for g in final_groups[:10])
    if len(final_groups) > 10:
        group_list += f"\n  ... va yana {len(final_groups) - 10} ta"

    await call.message.edit_text(
        f"✅ <b>Tayyor!</b>\n\n"
        f"👥 Tanlangan guruhlar: <b>{len(final_groups)}</b>\n\n"
        f"<b>Guruhlar:</b>\n{group_list if final_groups else '  <i>hech qaysi tanlanmadi</i>'}",
        parse_mode="HTML",
        reply_markup=kb_back_inline("groups:back")
    )
    await call.answer()


@router.callback_query(F.data == "gp_back")
async def cb_group_picker_back(call: CallbackQuery, state: FSMContext):
    _picker_all_groups.pop(call.from_user.id, None)
    _picker_selected_ids.pop(call.from_user.id, None)
    await call.message.edit_text(
        "📂 Guruhlar boshqaruvi:",
        reply_markup=kb_groups_menu()
    )
    await call.answer()


# ─── Boshqa (foydalanuvchi o'zi tayyorlagan) jildni tanlash ───────────────────

@router.callback_query(F.data == "groups:choose_folder")
async def cb_choose_folder(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)

    remaining = _warmup_remaining_seconds(user)
    if remaining > 0:
        minutes = remaining // 60 + (1 if remaining % 60 else 0)
        await call.message.edit_text(
            "⏳ <b>Hisobingiz hali \"isinib\" ulgurmadi</b>\n\n"
            "Xavfsizlik uchun yangi ulangan hisobda guruhlarni darhol "
            "olib bo'lmaydi — Telegram buni shubhali harakat deb "
            "hisoblab, hisobingizni chiqarib yuborishi mumkin edi.\n\n"
            f"⏱ Taxminan <b>{minutes} daqiqadan</b> so'ng qayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return

    loading = await call.message.edit_text("⏳ <b>Jildlar yuklanmoqda...</b>", parse_mode="HTML")

    try:
        folders = await telethon_service.get_dialog_folders(user.id, user.session_string)

        if not folders:
            await loading.edit_text(
                "❌ <b>Jildlar topilmadi.</b>\n\n"
                "Telegram ilovangizda avval jild yarating va unga guruhlarni "
                "qo'shib chiqing, so'ng shu tugmani qayta bosing.\n\n"
                "💡 Yoki bot uchun avtomatik jild yaratishni xohlasangiz, "
                "\"🆕 'Bot uchun' jild yaratish\" tugmasidan foydalaning.",
                parse_mode="HTML",
                reply_markup=kb_back_inline("groups:back")
            )
            await call.answer()
            return

        _user_folders[call.from_user.id] = folders
        await state.set_state(GroupFlow.choosing_folder)

        await loading.edit_text(
            f"📁 <b>Jild tanlang</b>\n\n"
            f"<b>{len(folders)}</b> ta jild topildi.\n"
            "E'lon yuboriladigan guruhlar joylashgan jildni tanlang:",
            parse_mode="HTML",
            reply_markup=kb_folders(folders)
        )

    except (telethon_service.SessionRevokedException, telethon_service.AccountBannedException) as e:
        banned = isinstance(e, telethon_service.AccountBannedException)
        await telethon_service.handle_lost_session(call.bot, user.id, call.from_user.id, banned=banned)
        await loading.edit_text(
            "🚫 <b>Akkauntingiz bloklangan.</b>\n\nBoshqa raqam bilan urinib ko'ring."
            if banned else
            "⚠️ <b>Hisobingiz uzilib qoldi!</b>\n\n"
            "Telegram hisobingiz uzilgani aniqlandi va bazadan tozalandi.\n"
            "Iltimos, <b>📱 Raqam qo'shish</b> orqali qayta ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return
    except Exception as e:
        logger.error(f"Jildlarni yuklashda xato {call.from_user.id}: {e}")
        await loading.edit_text(
            f"❌ <b>Guruhlarni yuklashda xato.</b>\n\n"
            f"<code>{str(e)[:200]}</code>\n\n"
            "Hisobingizni qayta ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("groups:back")
        )
    await call.answer()


@router.callback_query(GroupFlow.choosing_folder, F.data.startswith("folder:"))
async def cb_folder_selected(call: CallbackQuery, state: FSMContext):
    data = call.data.split(":")[1]

    if data == "back":
        await state.clear()
        user = await get_or_create_user(call.from_user.id)
        await call.message.edit_text(
            "📋 Asosiy menyu:",
            reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
        )
        await call.answer()
        return

    folder_idx = int(data)
    folders = _user_folders.get(call.from_user.id, [])

    if folder_idx >= len(folders):
        await call.answer("Noto'g'ri tanlov.", show_alert=True)
        return

    folder = folders[folder_idx]
    loading_msg = await call.message.edit_text(
        f"⏳ <b>'{folder['title']}' jildidan guruhlar import qilinmoqda...</b>",
        parse_mode="HTML"
    )

    user = await get_or_create_user(call.from_user.id)
    try:
        groups = await telethon_service.get_groups_from_folder(
            user.id, user.session_string, folder.get("filter")
        )

        if not groups:
            await loading_msg.edit_text(
                f"⚠️ <b>'{folder['title']}' jildida guruhlar topilmadi</b>\n\n"
                "Bu jild faqat kanallardan iborat yoki bo'sh bo'lishi mumkin.\n"
                "Boshqa jildni tanlang.",
                parse_mode="HTML",
                reply_markup=kb_folders(folders)
            )
            await call.answer()
            return

        await save_groups(user.id, groups, folder["title"])
        await state.clear()

        group_list = "\n".join(f"  • {g['title']}" for g in groups[:10])
        if len(groups) > 10:
            group_list += f"\n  ... va yana {len(groups) - 10} ta"

        await loading_msg.edit_text(
            f"✅ <b>Guruhlar muvaffaqiyatli import qilindi!</b>\n\n"
            f"📁 Jild: <b>{folder['title']}</b>\n"
            f"👥 Guruhlar: <b>{len(groups)}</b>\n\n"
            f"<b>Guruhlar:</b>\n{group_list}",
            parse_mode="HTML",
            reply_markup=kb_back_inline("groups:back")
        )

    except (telethon_service.SessionRevokedException, telethon_service.AccountBannedException) as e:
        banned = isinstance(e, telethon_service.AccountBannedException)
        await telethon_service.handle_lost_session(call.bot, user.id, call.from_user.id, banned=banned)
        await loading_msg.edit_text(
            "🚫 <b>Akkauntingiz bloklangan.</b>\n\nBoshqa raqam bilan urinib ko'ring."
            if banned else
            "⚠️ <b>Hisobingiz uzilib qoldi!</b>\n\n"
            "Telegram hisobingiz uzilgani aniqlandi va bazadan tozalandi.\n"
            "Iltimos, <b>📱 Raqam qo'shish</b> orqali qayta ulang.",
            parse_mode="HTML",
            reply_markup=kb_back_inline("menu:home")
        )
        await call.answer()
        return
    except Exception as e:
        logger.error(f"Guruhlarni import qilishda xato: {e}")
        await loading_msg.edit_text(
            f"❌ <b>Guruhlarni import qilishda xato.</b>\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML",
            reply_markup=kb_back_inline("groups:back")
        )

    await call.answer()


# ─── Guruhlarni tozalash ──────────────────────────────────────────────────────

@router.callback_query(F.data == "groups:clear")
async def cb_clear_groups_prompt(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "🗑 <b>Guruhlarni o'chirish</b>\n\n"
        "Barcha saqlangan guruhlar o'chiriladi.\n"
        "Davom etishni xohlaysizmi?",
        parse_mode="HTML",
        reply_markup=kb_confirm_clear_groups()
    )
    await call.answer()


@router.callback_query(F.data == "groups:confirm_clear")
async def cb_confirm_clear_groups(call: CallbackQuery, state: FSMContext):
    from app.database import async_session_maker
    from sqlalchemy import delete
    from app.database import Group

    user = await get_or_create_user(call.from_user.id)

    async with async_session_maker() as session:
        await session.execute(delete(Group).where(Group.user_id == user.id))
        await session.commit()

    await state.clear()
    await call.message.edit_text(
        "✅ <b>Barcha guruhlar o'chirildi!</b>\n\n"
        "Yangi guruhlar qo'shish uchun <b>📂 Guruh qo'shish</b> tugmasini bosing.",
        parse_mode="HTML"
    )
    await call.message.answer(
        "📋 Asosiy menyu:",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    )
    await call.answer("✅ O'chirildi!")


@router.callback_query(F.data == "groups:cancel_clear")
async def cb_cancel_clear_groups(call: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(call.from_user.id)
    await call.message.edit_text(
        "📋 Asosiy menyu:",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    )
    await call.answer()


@router.callback_query(F.data == "groups:back")
async def cb_groups_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(call.from_user.id)
    await call.message.edit_text(
        "📋 Asosiy menyu:",
        reply_markup=kb_main_menu(is_admin=user.is_admin, has_phone=bool(user.session_string))
    )
    await call.answer()