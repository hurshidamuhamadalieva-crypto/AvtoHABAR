from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import config

# ─────────────────────────────────────────────────────────────────────────────
# Deyarli barcha tugmalar endi INLINE (xabar ostida chiqadigan) tugmalar.
# Faqat bitta joy bundan mustasno: telefon raqamni "tugma orqali yuborish"
# (request_contact). Buni Telegram FAQAT oddiy (reply) klaviatura orqali
# qo'llab-quvvatlaydi — inline tugmada bunday funksiya umuman mavjud emas.
# ─────────────────────────────────────────────────────────────────────────────


def kb_plans() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, plan in config.PLANS.items():
        builder.button(text=plan["label"], callback_data=f"plan:{key}")
    builder.adjust(1)
    return builder.as_markup()


def kb_payment_methods() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨‍💼 Admin orqali to'lash", callback_data="pay:admin")
    builder.button(text="🏦 Karta o'tkazma + Screenshot", callback_data="pay:card")
    builder.button(text="🔙 Orqaga", callback_data="pay:back")
    builder.adjust(1)
    return builder.as_markup()


def kb_share_phone() -> ReplyKeyboardMarkup:
    """
    MUHIM: Telegram request_contact tugmasini faqat oddiy (reply)
    klaviaturada ishlatishga ruxsat beradi — shu sababli bu yerda
    istisno qilib qoldirilgan.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="📱 Kontaktni ulashish", request_contact=True)
    builder.button(text="✏️ Qo'lda kiritish")
    builder.button(text="❌ Bekor qilish")
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def kb_cancel_inline(callback: str = "generic:cancel") -> InlineKeyboardMarkup:
    """Matn/kod kutilayotgan bosqichlarda xabar ostida chiqadigan bekor qilish tugmasi."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data=callback)
    return builder.as_markup()


def kb_main_menu(is_admin: bool = False, has_phone: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    phone_label = "🔄 Raqamni boshqarish" if has_phone else "📱 Raqam qo'shish"
    builder.button(text=phone_label, callback_data="menu:phone")
    builder.button(text="📂 Guruh qo'shish", callback_data="menu:groups")
    builder.button(text="📢 E'lon yuborish", callback_data="menu:broadcast")
    builder.button(text="💰 To'lovlarim", callback_data="menu:payments")
    builder.button(text="👨‍💼 Admin bilan bog'lanish", callback_data="menu:contact_admin")
    if is_admin:
        builder.button(text="⚙️ Admin Panel", callback_data="menu:admin")
    builder.adjust(2)
    return builder.as_markup()


def kb_back_to_menu(text: str = "🔙 Asosiy menyu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data="menu:home")
    return builder.as_markup()


def kb_interval() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for mins in config.BROADCAST_INTERVALS:
        builder.button(text=f"⏱ {mins} daqiqa", callback_data=f"bc_interval:{mins}")
    builder.button(text="❌ Bekor qilish", callback_data="bc_cancel")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def kb_broadcast_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash va boshlash", callback_data="bc_confirm")
    builder.button(text="🗑 Bekor qilish", callback_data="bc_cancel")
    builder.adjust(1)
    return builder.as_markup()


def kb_broadcast_menu(is_sending: bool, has_ad: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_sending:
        builder.button(text="⏸ Pauza", callback_data="bc_pause")
        builder.button(text="⏹ To'xtatish", callback_data="bc_stop")
    else:
        if has_ad:
            builder.button(text="♻️ Oxirgi e'londan davom etish", callback_data="bc_continue")
        builder.button(text="🆕 Yangi e'lon yozish", callback_data="bc_new")
    builder.button(text="🔙 Asosiy menyu", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def kb_broadcast_sending(is_paused: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_paused:
        builder.button(text="▶️ Davom ettirish", callback_data="bc_resume")
    else:
        builder.button(text="⏸ Pauza", callback_data="bc_pause")
    builder.button(text="⏹ To'xtatish", callback_data="bc_stop")
    builder.adjust(2)
    return builder.as_markup()


def kb_admin_panel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Foydalanuvchilar", callback_data="admin:users")
    builder.button(text="💰 To'lovlar", callback_data="admin:payments")
    builder.button(text="📊 Statistika", callback_data="admin:stats")
    builder.button(text="📢 Xabar yuborish", callback_data="admin:broadcast")
    builder.button(text="🚫 Bloklash", callback_data="admin:ban")
    builder.button(text="✅ Foydalanuvchini tasdiqlash", callback_data="admin:approve")
    builder.button(text="⏹ Yuborishni to'xtatish", callback_data="admin:stop_sending")
    builder.button(text="🔙 Asosiy menyu", callback_data="menu:home")
    builder.adjust(2)
    return builder.as_markup()


def kb_approve_payment(payment_id: int, user_tg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data=f"admin_pay:approve:{payment_id}:{user_tg_id}")
    builder.button(text="❌ Rad etish", callback_data=f"admin_pay:reject:{payment_id}:{user_tg_id}")
    builder.adjust(2)
    return builder.as_markup()


def kb_folders(folders: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, folder in enumerate(folders):
        builder.button(text=f"📁 {folder['title']}", callback_data=f"folder:{i}")
    builder.button(text="🔙 Orqaga", callback_data="folder:back")
    builder.adjust(1)
    return builder.as_markup()


def kb_groups_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🆕 'Bot uchun' jild yaratish/yangilash", callback_data="groups:bot_folder")
    builder.button(text="📁 Boshqa (o'zim tayyorlagan) jildni tanlash", callback_data="groups:choose_folder")
    builder.button(text="🗑 Guruhlarni tozalash", callback_data="groups:clear")
    builder.button(text="🔙 Orqaga", callback_data="groups:back")
    builder.adjust(1)
    return builder.as_markup()


def kb_bot_folder_actions() -> InlineKeyboardMarkup:
    """'Bot uchun' jildi ekranida ko'rsatiladigan tugmalar."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Guruhlarni qo'shish", callback_data="gp_open:0")
    builder.button(text="🔙 Orqaga", callback_data="groups:back")
    builder.adjust(1)
    return builder.as_markup()


def kb_group_picker(groups_page: list, selected_ids: set, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """
    Guruhlarni sahifalab (10 tadan), tugma bosib tanlash/bekor qilish
    uchun klaviatura. Tanlangan guruh ✅, tanlanmagan ➕ belgisi bilan
    ko'rsatiladi.
    """
    builder = InlineKeyboardBuilder()
    for g in groups_page:
        is_selected = abs(g["id"]) in selected_ids
        mark = "✅" if is_selected else "➕"
        title = g["title"] if len(g["title"]) <= 32 else g["title"][:29] + "..."
        builder.button(text=f"{mark} {title}", callback_data=f"gp:{page}:{g['id']}")

    nav_count = 0
    if page > 0:
        builder.button(text="◀️ Oldingi", callback_data=f"gp_open:{page - 1}")
        nav_count += 1
    if page < total_pages - 1:
        builder.button(text="Keyingisi ▶️", callback_data=f"gp_open:{page + 1}")
        nav_count += 1

    builder.button(text="✅ Tayyor", callback_data="gp_done")
    builder.button(text="🔙 Orqaga", callback_data="gp_back")

    rows = [1] * len(groups_page)
    if nav_count:
        rows.append(nav_count)
    rows.append(1)  # Tayyor
    rows.append(1)  # Orqaga
    builder.adjust(*rows)

    return builder.as_markup()


def kb_confirm_clear_groups() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, o'chirish", callback_data="groups:confirm_clear")
    builder.button(text="❌ Bekor qilish", callback_data="groups:cancel_clear")
    builder.adjust(2)
    return builder.as_markup()


def kb_phone_menu(has_phone: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_phone:
        builder.button(text="🔄 Raqamni almashtirish", callback_data="phone:agree")
        builder.button(text="🗑 Raqamni uzish (session o'chirish)", callback_data="phone:disconnect")
    else:
        builder.button(text="✅ Roziman, davom etish", callback_data="phone:agree")
    builder.button(text="🔙 Menyuga qaytish", callback_data="phone:cancel")
    builder.adjust(1)
    return builder.as_markup()


def kb_confirm_disconnect() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, uzish", callback_data="phone:confirm_disconnect")
    builder.button(text="❌ Bekor qilish", callback_data="phone:cancel")
    builder.adjust(2)
    return builder.as_markup()


def kb_back_inline(callback: str = "back") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Orqaga", callback_data=callback)
    return builder.as_markup()


def kb_contact_admin() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨‍💼 Admin chatini ochish", url=f"https://t.me/{config.ADMIN_USERNAME}")
    builder.button(text="🔙 Asosiy menyu", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


remove_kb = ReplyKeyboardRemove()