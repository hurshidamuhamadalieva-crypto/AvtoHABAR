import asyncio
import itertools
import logging
import os
import random
import time
from typing import Optional, List, Dict

from telethon import TelegramClient, errors
from telethon.tl.types import (
    DialogFilter, Channel, Chat
)
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.sessions import StringSession

from config import config

logger = logging.getLogger(__name__)

_clients: Dict[int, TelegramClient] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Proksi rotatsiyasi + qurilma fingerprint randomizatsiyasi.
#
# SABAB: bitta serverdan (masalan, Railway) ko'plab akkauntlar UCHUN ketma-ket
# "kod yubor" (send_code) so'rovi bir xil IP + bir xil "qurilma" bilan borsa,
# Telegramning firibgarlikka qarshi tizimi buni shubhali (bot-farm) deb topib,
# ma'lum sondan keyin ba'zi so'rovlarga kodni jo'natishni cheklab/kechiktirib
# qo'yadi (so'rov "muvaffaqiyatli" qaytsa ham, kod haqiqatda kelmaydi).
# Shu sababli PROXIES sozlangan bo'lsa, har bir yangi login urinishi navbatdagi
# boshqa proksidan va boshqa (tasodifiy) qurilma fingerprint bilan yuboriladi.
# ─────────────────────────────────────────────────────────────────────────────

_proxy_counter = itertools.count()
_proxy_lock = asyncio.Lock()

# Haqiqiy, keng tarqalgan qurilmalar ko'rinishidagi fingerprintlar to'plami —
# har bir yangi login shulardan tasodifiy birini oladi.
DEVICE_PROFILES = [
    ("PC 64bit", "Windows 10", "Telegram Desktop 4.16.8"),
    ("PC 64bit", "Windows 11", "Telegram Desktop 4.16.4"),
    ("Samsung SM-G991B", "Android 13", "Telegram Android 10.9.2"),
    ("Samsung SM-A536B", "Android 14", "Telegram Android 10.12.0"),
    ("Xiaomi Redmi Note 12", "Android 13", "Telegram Android 10.10.1"),
    ("iPhone 13", "iOS 17.4", "Telegram iOS 10.9.3"),
    ("iPhone 14 Pro", "iOS 17.5", "Telegram iOS 10.10.0"),
    ("MacBook Pro", "macOS 14.4", "Telegram macOS 10.9"),
]

_last_send_code_at = 0.0
MIN_GAP_SECONDS = 4  # ketma-ket kod so'rovlari orasidagi eng kam pauza


class FloodWaitException(Exception):
    """Telegram vaqtincha yangi kod/kirish so'rovlarini cheklaganda ko'tariladi."""
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"FloodWait: {seconds}s")


class SessionRevokedException(Exception):
    """
    Foydalanuvchining sessiyasi Telegram tomonidan bekor qilingan/chiqarib
    yuborilgan (masalan, foydalanuvchi qurilmalar ro'yxatidan o'chirgan,
    yoki Telegramning xavfsizlik tizimi shubhali deb topib yopgan).
    Bu holatda foydalanuvchi hisobni QAYTA ulashi kerak.
    """
    pass


class AccountBannedException(Exception):
    """
    Telegram AKKAUNTNING O'ZINI (raqamning o'zini) bloklagan — bu
    sessiyaning bekor qilinishidan farqli, akkauntning o'zi ishlamay
    qoladi, qayta ulash yordam bermaydi.
    """
    pass


def _proxy_key(proxy_tuple) -> Optional[str]:
    if not proxy_tuple:
        return None
    return f"{proxy_tuple[1]}:{proxy_tuple[2]}"


def _find_proxy_by_key(key: str):
    if not key or not config.PROXIES:
        return None
    for p in config.PROXIES:
        if _proxy_key(p) == key:
            return p
    return None


async def _next_proxy():
    """Navbatdagi (proxy_key, proxy_tuple) juftligini qaytaradi. Proksi sozlanmagan bo'lsa (None, None)."""
    if not config.PROXIES:
        return None, None
    async with _proxy_lock:
        idx = next(_proxy_counter) % len(config.PROXIES)
        proxy = config.PROXIES[idx]
        return _proxy_key(proxy), proxy


def _random_device():
    return random.choice(DEVICE_PROFILES)


async def create_client(
    user_db_id: int,
    session_string: str = None,
    proxy=None,
    device_profile: tuple = None,
) -> TelegramClient:
    if session_string:
        session = StringSession(session_string)
    else:
        session = StringSession()

    device_model, system_version, app_version = device_profile or ("PC 64bit", "Windows 10", "Telegram Desktop")

    client = TelegramClient(
        session,
        config.API_ID,
        config.API_HASH,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code="uz",
        system_lang_code="uz-UZ",
        proxy=proxy,
        connection_retries=3,
        timeout=15,
    )
    return client


_client_locks: Dict[int, asyncio.Lock] = {}


def _get_client_lock(user_db_id: int) -> asyncio.Lock:
    """
    Har bir foydalanuvchi uchun ALOHIDA lock — bir vaqtning o'zida ikkita
    joy (masalan, "Guruh qo'shish" bosilishi va fon jarayonidagi yuborish
    sikli) bir xil sessiya bilan PARALLEL ravishda ikkita ulanish
    ochib yubormasligi uchun. Bu MUHIM: Telegram bitta sessiya (auth key)
    bir vaqtda bir nechta joydan parallel ulanganini "shubhali/dublikat"
    deb aniqlab, sessiyani avtomatik bekor qilishi mumkin edi —
    aynan shu turdagi tasodifiy poyga holati (race condition) sababli.
    """
    lock = _client_locks.get(user_db_id)
    if lock is None:
        lock = asyncio.Lock()
        _client_locks[user_db_id] = lock
    return lock


async def _safe_disconnect(client: TelegramClient):
    try:
        await client.disconnect()
    except Exception:
        pass


async def get_client(user_db_id: int, session_string: str) -> Optional[TelegramClient]:
    """
    Get or restore a client from session string.

    MUHIM:
    - Foydalanuvchi login qilganda unga biriktirilgan proksi va qurilma
      fingerprintini DB'dan o'qib, AYNAN o'shani qayta ishlatadi (login
      va keyingi foydalanish bir xil IP'dan bo'lishi uchun).
    - Har bir foydalanuvchi uchun lock ostida ishlaydi — bir xil sessiya
      bilan ikkita parallel ulanish hech qachon ochilmaydi.
    - Sessiya Telegram tomonidan bekor qilingan/akkaunt bloklangan bo'lsa,
      buni ANIQ aniqlab (SessionRevokedException / AccountBannedException),
      chaqiruvchi tomonga signal beradi — shunda bot foydalanuvchiga
      DARHOL to'g'ri xabar bera oladi va bazani tozalab, "ulanmagan"
      holatiga qaytaradi (aks holda foydalanuvchi buni sezmay, bot esa
      "sukut bo'yicha ishlamay qolgan" holatda qolib ketardi).
    """
    lock = _get_client_lock(user_db_id)
    async with lock:
        client = _clients.get(user_db_id)
        if client is not None:
            try:
                if not client.is_connected():
                    await client.connect()
                authorized = await client.is_user_authorized()
            except errors.UserDeactivatedBanError:
                await _safe_disconnect(client)
                _clients.pop(user_db_id, None)
                raise AccountBannedException("Akkaunt Telegram tomonidan bloklangan.")
            except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.AuthKeyDuplicatedError) as e:
                await _safe_disconnect(client)
                _clients.pop(user_db_id, None)
                raise SessionRevokedException(f"Sessiya bekor qilingan: {e}")
            except Exception as e:
                logger.warning(f"Client reconnect failed for user {user_db_id}: {e}")
                await _safe_disconnect(client)
                _clients.pop(user_db_id, None)
                client = None
            else:
                if authorized:
                    return client
                await _safe_disconnect(client)
                _clients.pop(user_db_id, None)
                raise SessionRevokedException("Sessiya endi amal qilmaydi.")

        proxy, device = await _get_assigned_proxy_and_device(user_db_id)

        try:
            client = await create_client(user_db_id, session_string, proxy=proxy, device_profile=device)
            await client.connect()
            authorized = await client.is_user_authorized()
        except errors.UserDeactivatedBanError:
            raise AccountBannedException("Akkaunt Telegram tomonidan bloklangan.")
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.AuthKeyDuplicatedError) as e:
            raise SessionRevokedException(f"Sessiya bekor qilingan: {e}")
        except Exception as e:
            logger.error(f"Failed to restore client for user {user_db_id}: {e}")
            return None

        if authorized:
            _clients[user_db_id] = client
            return client

        await _safe_disconnect(client)
        raise SessionRevokedException("Sessiya endi amal qilmaydi (yangi ulanishda).")


async def _get_assigned_proxy_and_device(user_db_id: int):
    """Foydalanuvchiga login paytida biriktirilgan proksi/qurilmani DB'dan o'qiydi."""
    try:
        from app.database import get_user_by_db_id  # lazy import — aylanma import'dan qochish uchun
        user = await get_user_by_db_id(user_db_id)
        if not user:
            return None, None

        proxy = None
        proxy_key = getattr(user, "proxy_key", None)
        if proxy_key:
            proxy = _find_proxy_by_key(proxy_key)
            if proxy is None:
                logger.warning(
                    f"Foydalanuvchi {user_db_id} uchun biriktirilgan proksi "
                    f"({proxy_key}) endi PROXIES ro'yxatida yo'q — proksisiz ulanadi."
                )
        elif user.proxy_index is not None and config.PROXIES and 0 <= user.proxy_index < len(config.PROXIES):
            # Legacy: eski (proxy_key qo'shilishidan oldingi) akkauntlar uchun zaxira yo'l
            proxy = config.PROXIES[user.proxy_index]

        device = None
        if user.device_model:
            device = (
                user.device_model,
                user.device_system_version or "Windows 10",
                user.device_app_version or "Telegram Desktop",
            )
        return proxy, device
    except Exception as e:
        logger.warning(f"Foydalanuvchi {user_db_id} uchun proksi/qurilma ma'lumotini o'qib bo'lmadi: {e}")
        return None, None


async def send_code(phone: str) -> tuple:
    """
    Telefon raqamga kirish kodini yuboradi.

    Qaytaradi: (client, result, proxy_key, device_profile)
    — chaqiruvchi tomon (phone.py) muvaffaqiyatli kirishdan so'ng shu
    proxy_key va device_profile'ni foydalanuvchiga DOIMIY biriktirib
    saqlashi kerak, shunda keyingi barcha ulanishlar (get_client orqali)
    ham AYNAN shu proksi/qurilmadan foydalanadi.

    - Sozlangan bo'lsa, navbatdagi proksidan foydalanadi (rotatsiya) —
      shunda ketma-ket kelgan urinishlar boshqa-boshqa IP'dan ko'rinadi.
    - Har safar tasodifiy (lekin haqiqiy) qurilma fingerprinti tanlanadi.
    - So'rovlar orasida eng kamida MIN_GAP_SECONDS pauza saqlanadi (portlash
      shaklidagi so'rovlar Telegram tomonidan tezroq shubhali deb topiladi).
    - Agar biror proksi ishlamasa (masalan, o'lik/bloklangan bo'lsa),
      navbatdagi proksiga avtomatik o'tiladi.
    - Telegram FloodWait qaytarsa, aniq kutish vaqti bilan FloodWaitException
      ko'tariladi (chaqiruvchi tomon foydalanuvchiga tushunarli xabar
      ko'rsatishi uchun).
    """
    global _last_send_code_at

    async with _proxy_lock:
        elapsed = time.time() - _last_send_code_at
        if elapsed < MIN_GAP_SECONDS:
            await asyncio.sleep(MIN_GAP_SECONDS - elapsed)
        _last_send_code_at = time.time()

    attempts = max(1, len(config.PROXIES)) if config.PROXIES else 1
    last_error = None

    for attempt in range(attempts):
        proxy_key, proxy = await _next_proxy()
        device = _random_device()
        client = await create_client(0, proxy=proxy, device_profile=device)

        try:
            await client.connect()
        except Exception as e:
            logger.warning(f"Proksi bilan ulanib bo'lmadi ({proxy}): {e}. Keyingisiga o'tilmoqda...")
            last_error = e
            try:
                await client.disconnect()
            except Exception:
                pass
            continue

        try:
            result = await client.send_code_request(phone)
            if proxy:
                logger.info(f"Kod so'rovi yuborildi: {phone} — proksi orqali ({proxy_key})")
            else:
                logger.info(f"Kod so'rovi yuborildi: {phone} — proksisiz (to'g'ridan-to'g'ri)")
            return client, result, proxy_key, device

        except errors.FloodWaitError as e:
            logger.warning(f"FloodWait {e.seconds}s — {phone} uchun kod so'rovida (proksi: {proxy})")
            try:
                await client.disconnect()
            except Exception:
                pass
            raise FloodWaitException(e.seconds)

        except Exception as e:
            last_error = e
            try:
                await client.disconnect()
            except Exception:
                pass
            # Ulanish/tarmoq xatosi bo'lsa keyingi proksini sinaymiz;
            # aks holda (masalan, noto'g'ri raqam) darhol xatoni ko'taramiz.
            if attempt < attempts - 1 and isinstance(e, (ConnectionError, OSError, asyncio.TimeoutError)):
                continue
            raise

    raise last_error or RuntimeError("Kod yuborib bo'lmadi: barcha proksilar ishlamadi.")


async def sign_in(client: TelegramClient, phone: str, code: str, phone_code_hash: str, password: str = None):
    """Sign in with code (and optionally 2FA password)."""
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except errors.SessionPasswordNeededError:
        if password:
            await client.sign_in(password=password)
        else:
            raise
    session_string = client.session.save()
    return session_string


async def get_dialog_folders(user_db_id: int, session_string: str) -> List[Dict]:
    """Get all dialog filters (folders) for a user."""
    client = await get_client(user_db_id, session_string)
    if not client:
        return []

    try:
        result = await client(GetDialogFiltersRequest())
        folders = []
        for f in result.filters:
            if hasattr(f, 'title'):
                folders.append({
                    "id": f.id,
                    "title": f.title,
                    "filter": f,
                })
        return folders
    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"Error getting folders for user {user_db_id}: {e}")
        return []


BOT_FOLDER_TITLE = "Bot uchun"


async def _resolve_groups_from_peers(client, peers) -> List[Dict]:
    """InputPeer ro'yxatini (jild.include_peers) haqiqiy guruh ma'lumotlariga aylantiradi."""
    groups = []
    count = 0
    for peer in peers:
        try:
            entity = await client.get_entity(peer)
            is_group = isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup)
            if is_group:
                groups.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                })
        except Exception as e:
            logger.warning(f"Peer'ni aniqlashda xato: {e}")
        count += 1
        if count % 20 == 0:
            await asyncio.sleep(random.uniform(0.25, 0.5))
    return groups


async def get_or_create_bot_folder(user_db_id: int, session_string: str) -> tuple:
    """
    "Bot uchun" nomli Telegram jildini topadi — topilmasa, BO'SH holda
    o'zi yaratadi (guruhlarni AVTOMATIK qo'shmaydi — foydalanuvchi kerakli
    guruhlarni Telegram ilovasida o'zi, qo'lda shu jildga qo'shadi).

    Jild allaqachon mavjud bo'lsa, undagi (foydalanuvchi o'zi qo'shib
    qo'ygan) guruhlar ro'yxati o'qib qaytariladi.

    Qaytaradi: (groups: List[Dict], created: bool)
    - created=True  → jild ENDI yaratildi (bo'sh, hali guruh yo'q)
    - created=False → jild allaqachon bor edi, undagi guruhlar qaytarildi
    """
    client = await get_client(user_db_id, session_string)
    if not client:
        return [], False

    try:
        result = await client(GetDialogFiltersRequest())
        existing_filters = [f for f in result.filters if hasattr(f, 'title')]

        bot_filter = next((f for f in existing_filters if f.title == BOT_FOLDER_TITLE), None)

        if bot_filter:
            groups = await _resolve_groups_from_peers(client, bot_filter.include_peers)
            return groups, False

        # Jild topilmadi — BO'SH holda yaratamiz. Guruhlarni AVTOMATIK
        # qo'shmaymiz — bu foydalanuvchining o'zi bajaradigan qadam.
        used_ids = {f.id for f in existing_filters}
        new_id = 2
        while new_id in used_ids:
            new_id += 1

        new_filter = DialogFilter(
            id=new_id,
            title=BOT_FOLDER_TITLE,
            pinned_peers=[],
            include_peers=[],
            exclude_peers=[],
        )
        await client(UpdateDialogFilterRequest(id=new_id, filter=new_filter))

        # Yaratilgani HAQIQATDA amalga oshganini tasdiqlash uchun qayta
        # so'raymiz (ba'zida server javobi kechikishi yoki mijoz keshi
        # eskirgan bo'lishi mumkin — shu sabab avval "topildi" deb noto'g'ri
        # xabar berilib qolgan holatlar bo'lgan edi).
        verify = await client(GetDialogFiltersRequest())
        confirmed = any(
            hasattr(f, 'title') and f.title == BOT_FOLDER_TITLE for f in verify.filters
        )
        if not confirmed:
            logger.error(f"'{BOT_FOLDER_TITLE}' jildi yaratildi, lekin tasdiqlanmadi (user {user_db_id})")

        logger.info(f"'{BOT_FOLDER_TITLE}' bo'sh jildi yaratildi: user {user_db_id}, tasdiqlandi={confirmed}")

        return [], True

    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"get_or_create_bot_folder xatosi (user {user_db_id}): {e}")
        return [], False


async def list_all_groups_with_selection(user_db_id: int, session_string: str) -> tuple:
    """
    Foydalanuvchining BARCHA guruhlarini, shu bilan birga ulardan qaysilari
    hozir "Bot uchun" jildida borligini (tanlangan holatini) birga qaytaradi.
    Bot ichidagi tugma orqali guruh tanlash ekranini chizish uchun ishlatiladi.

    Qaytaradi: (all_groups: List[Dict], selected_ids: set)
    """
    client = await get_client(user_db_id, session_string)
    if not client:
        return [], set()

    try:
        result = await client(GetDialogFiltersRequest())
        existing_filters = [f for f in result.filters if hasattr(f, 'title')]
        bot_filter = next((f for f in existing_filters if f.title == BOT_FOLDER_TITLE), None)

        selected_ids = set()
        if bot_filter:
            for p in bot_filter.include_peers:
                pid = getattr(p, 'channel_id', None) or getattr(p, 'chat_id', None)
                if pid is not None:
                    selected_ids.add(pid)

        groups = []
        count = 0
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            is_group = isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup)
            if is_group:
                groups.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                })
            count += 1
            if count % 20 == 0:
                await asyncio.sleep(random.uniform(0.25, 0.5))

        return groups, selected_ids

    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"list_all_groups_with_selection xatosi (user {user_db_id}): {e}")
        return [], set()


async def toggle_group_in_bot_folder(
    user_db_id: int, session_string: str, group_id: int, add: bool
) -> bool:
    """
    "Bot uchun" jildiga bitta guruhni qo'shadi (add=True) yoki undan
    chiqaradi (add=False). Jild mavjud bo'lmasa, avval bo'sh holda
    yaratib olinadi.
    """
    client = await get_client(user_db_id, session_string)
    if not client:
        return False

    try:
        result = await client(GetDialogFiltersRequest())
        existing_filters = [f for f in result.filters if hasattr(f, 'title')]
        bot_filter = next((f for f in existing_filters if f.title == BOT_FOLDER_TITLE), None)

        if not bot_filter:
            used_ids = {f.id for f in existing_filters}
            new_id = 2
            while new_id in used_ids:
                new_id += 1
            bot_filter = DialogFilter(
                id=new_id, title=BOT_FOLDER_TITLE,
                pinned_peers=[], include_peers=[], exclude_peers=[],
            )

        entity = await client.get_entity(group_id)
        peer = await client.get_input_entity(entity)
        target_id = abs(group_id)

        new_include = []
        already_present = False
        for p in bot_filter.include_peers:
            pid = abs(getattr(p, 'channel_id', None) or getattr(p, 'chat_id', None) or 0)
            if pid == target_id:
                already_present = True
                if add:
                    new_include.append(p)  # allaqachon bor, qoldiramiz
                # add=False bo'lsa — bu peer'ni ro'yxatga qo'shmaymiz (o'chirilgan bo'ladi)
            else:
                new_include.append(p)

        if add and not already_present:
            new_include.append(peer)

        updated_filter = DialogFilter(
            id=bot_filter.id,
            title=BOT_FOLDER_TITLE,
            pinned_peers=list(bot_filter.pinned_peers),
            include_peers=new_include,
            exclude_peers=list(bot_filter.exclude_peers),
        )
        await client(UpdateDialogFilterRequest(id=bot_filter.id, filter=updated_filter))
        return True

    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"toggle_group_in_bot_folder xatosi (user {user_db_id}, group {group_id}): {e}")
        return False


async def get_groups_from_folder(user_db_id: int, session_string: str, folder_filter) -> List[Dict]:
    """Extract groups from a dialog filter folder."""
    client = await get_client(user_db_id, session_string)
    if not client:
        return []

    groups = []
    try:
        # Barcha suhbatlarni olish — MUHIM: bitta uzluksiz "portlash" tarzida
        # emas, insonga o'xshab, bir necha suhbatdan keyin qisqa pauza bilan.
        # Yangi sessiyada darhol yuzlab suhbatni bir zumda so'rash Telegramning
        # firibgarlikka qarshi tizimiga "akkaunt skraping qilinyapti" signalini
        # berib, akkauntni avtomatik chiqarib yuborishiga sabab bo'lgan edi.
        count = 0
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, (Channel, Chat)):
                # Check if it's a group (not a channel/broadcast)
                is_group = False
                if isinstance(entity, Chat):
                    is_group = True
                elif isinstance(entity, Channel) and entity.megagroup:
                    is_group = True

                if is_group:
                    groups.append({
                        "id": entity.id,
                        "title": entity.title,
                        "username": getattr(entity, "username", None),
                    })

            count += 1
            if count % 20 == 0:
                await asyncio.sleep(random.uniform(0.25, 0.5))

        # Filter by folder if folder_filter has include_peers
        if folder_filter and hasattr(folder_filter, 'include_peers') and folder_filter.include_peers:
            folder_ids = set()
            for peer in folder_filter.include_peers:
                if hasattr(peer, 'channel_id'):
                    folder_ids.add(peer.channel_id)
                elif hasattr(peer, 'chat_id'):
                    folder_ids.add(peer.chat_id)

            if folder_ids:
                groups = [g for g in groups if abs(g["id"]) in folder_ids]

    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"Error getting groups for user {user_db_id}: {e}")

    return groups


async def get_all_groups(user_db_id: int, session_string: str) -> List[Dict]:
    """Get all groups the user is member of."""
    client = await get_client(user_db_id, session_string)
    if not client:
        return []

    groups = []
    try:
        count = 0
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, Chat):
                groups.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": None,
                })
            elif isinstance(entity, Channel) and entity.megagroup:
                groups.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                })

            count += 1
            if count % 20 == 0:
                await asyncio.sleep(random.uniform(0.25, 0.5))

    except (SessionRevokedException, AccountBannedException):
        raise
    except Exception as e:
        logger.error(f"Error getting all groups for user {user_db_id}: {e}")

    return groups


async def send_message_to_group(
    user_db_id: int,
    session_string: str,
    group_id: int,
    text: str,
    group_username: str = None,
) -> tuple[bool, str]:
    """Send a message to a group. Returns (success, error_msg)."""
    client = await get_client(user_db_id, session_string)
    if not client:
        return False, "Client not available"

    for attempt in range(3):
        try:
            if group_username:
                entity = group_username
            else:
                entity = group_id

            await client.send_message(entity, text)
            return True, ""

        except errors.FloodWaitError as e:
            wait = e.seconds
            logger.warning(f"FloodWait {wait}s for user {user_db_id} in group {group_id}")
            if wait > 300:
                return False, f"FloodWait: {wait}s"
            await asyncio.sleep(wait + 5)

        except errors.UserBannedInChannelError:
            return False, "Banned in channel"

        except errors.ChatWriteForbiddenError:
            return False, "Write forbidden"

        except errors.SlowModeWaitError as e:
            return False, f"SlowMode: {e.seconds}s"

        except errors.RPCError as e:
            logger.error(f"RPC error sending to {group_id}: {e}")
            if attempt == 2:
                return False, str(e)
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Unexpected error sending to {group_id}: {e}")
            return False, str(e)

    return False, "Max retries exceeded"


async def disconnect_client(user_db_id: int):
    """Disconnect and remove a client from pool."""
    if user_db_id in _clients:
        try:
            await _clients[user_db_id].disconnect()
        except Exception:
            pass
        del _clients[user_db_id]


async def _clear_user_session(user_tg_id: int):
    from app.database import update_user
    await update_user(
        user_tg_id,
        session_string=None,
        phone_number=None,
        proxy_index=None,
        proxy_key=None,
        device_model=None,
        device_system_version=None,
        device_app_version=None,
        session_connected_at=None,
    )


async def _notify_session_lost(bot, user_tg_id: int, banned: bool):
    text = (
        "🚫 <b>Akkauntingiz Telegram tomonidan bloklangan.</b>\n\n"
        "Afsuski, bu holatda akkauntni qayta ulash yordam bermaydi — "
        "boshqa raqam bilan urinib ko'ring."
        if banned else
        "⚠️ <b>Diqqat: hisobingiz uzilib qoldi!</b>\n\n"
        "Telegram hisobingiz sabab (masalan, xavfsizlik tekshiruvi) bilan "
        "uzildi va bot endi undan foydalana olmaydi.\n\n"
        "Iltimos, <b>📱 Raqam qo'shish</b> orqali hisobingizni qayta ulang."
    )
    try:
        await bot.send_message(user_tg_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Foydalanuvchi {user_tg_id} ga sessiya-uzilish xabarini yuborib bo'lmadi: {e}")


async def handle_lost_session(bot, user_db_id: int, user_tg_id: int, banned: bool = False):
    """
    Sessiya bekor qilingani/akkaunt bloklangani ANIQLANGANDA chaqiriladi
    (sending sikli yoki guruh olish paytida, yoxud fon health-check
    tomonidan). Bazani darhol tozalaydi (bot to'g'ri "ulanmagan" holatini
    ko'rsatishi uchun), aktiv yuborishni to'xtatadi va foydalanuvchiga
    DARHOL xabar beradi.
    """
    logger.warning(f"Sessiya yo'qoldi: user_db_id={user_db_id}, banned={banned}")

    try:
        from app.services.sender_service import stop_all_for_user
        await stop_all_for_user(user_db_id)
    except Exception as e:
        logger.warning(f"stop_all_for_user xatosi ({user_db_id}): {e}")

    await disconnect_client(user_db_id)
    await _clear_user_session(user_tg_id)
    await _notify_session_lost(bot, user_tg_id, banned)


async def session_health_checker(bot):
    """
    Fon vazifasi: har SESSION_HEALTH_CHECK_MINUTES daqiqada barcha ulangan
    akkauntlarning sessiyasi hali ham amal qilayotganini YENGIL tekshiradi
    (faqat is_user_authorized — hech qanday og'ir/ko'p so'rov yo'q, shuning
    uchun bu tekshiruvning o'zi hech kimning akkauntiga xavf tug'dirmaydi).

    Maqsad: Telegram biror sessiyani bekor qilsa, buni foydalanuvchi
    o'zi payqamasdan (masalan, keyingi safar yuborish ishlamay qolganda
    tasodifan bilib qolish o'rniga), bot DARHOL aniqlab, bazani to'g'ri
    holatga qaytarib, foydalanuvchiga xabar beradi.
    """
    while True:
        await asyncio.sleep(config.SESSION_HEALTH_CHECK_MINUTES * 60)
        try:
            from app.database import get_all_users
            users = await get_all_users()
            for user in users:
                if not user.session_string:
                    continue
                try:
                    await get_client(user.id, user.session_string)
                except SessionRevokedException:
                    await handle_lost_session(bot, user.id, user.telegram_id, banned=False)
                except AccountBannedException:
                    await handle_lost_session(bot, user.id, user.telegram_id, banned=True)
                except Exception as e:
                    logger.warning(f"session_health_checker: user {user.id} vaqtinchalik xato: {e}")
                # Hammasini bir zumda tekshirmaslik — orasiga qisqa pauza
                await asyncio.sleep(random.uniform(2, 5))
        except Exception as e:
            logger.error(f"session_health_checker umumiy xatosi: {e}", exc_info=True)