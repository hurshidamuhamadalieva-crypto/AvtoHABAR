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


async def _next_proxy():
    """Navbatdagi (index, proxy_tuple) juftligini qaytaradi. Proksi sozlanmagan bo'lsa (None, None)."""
    if not config.PROXIES:
        return None, None
    async with _proxy_lock:
        idx = next(_proxy_counter) % len(config.PROXIES)
        return idx, config.PROXIES[idx]


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


async def get_client(user_db_id: int, session_string: str) -> Optional[TelegramClient]:
    """
    Get or restore a client from session string.

    MUHIM: Bu foydalanuvchi login qilganda unga biriktirilgan proksi va
    qurilma fingerprintini DB'dan o'qib, AYNAN o'shani qayta ishlatadi.
    Shu orqali "login boshqa IP'dan, keyingi foydalanish boshqa IP'dan"
    bo'lib, Telegram akkauntni avtomatik chiqarib yubormasligi ta'minlanadi.
    Bunday biriktirilgan proksi topilmasa (masalan, eski, proksi tizimi
    qo'shilishidan oldin ulangan akkauntlar) — proksisiz, to'g'ridan-to'g'ri
    ulanadi (ular ham shunday ulangan edi, shuning uchun bu izchillikni
    buzmaydi).
    """
    if user_db_id in _clients:
        client = _clients[user_db_id]
        if client.is_connected():
            return client
        try:
            await client.connect()
            if await client.is_user_authorized():
                return client
        except Exception as e:
            logger.warning(f"Client reconnect failed for user {user_db_id}: {e}")
        del _clients[user_db_id]

    proxy, device = await _get_assigned_proxy_and_device(user_db_id)

    try:
        client = await create_client(user_db_id, session_string, proxy=proxy, device_profile=device)
        await client.connect()
        if await client.is_user_authorized():
            _clients[user_db_id] = client
            return client
    except Exception as e:
        logger.error(f"Failed to restore client for user {user_db_id}: {e}")
    return None


async def _get_assigned_proxy_and_device(user_db_id: int):
    """Foydalanuvchiga login paytida biriktirilgan proksi/qurilmani DB'dan o'qiydi."""
    try:
        from app.database import get_user_by_db_id  # lazy import — aylanma import'dan qochish uchun
        user = await get_user_by_db_id(user_db_id)
        if not user:
            return None, None

        proxy = None
        if user.proxy_index is not None and config.PROXIES and 0 <= user.proxy_index < len(config.PROXIES):
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

    Qaytaradi: (client, result, proxy_index, device_profile)
    — chaqiruvchi tomon (phone.py) muvaffaqiyatli kirishdan so'ng shu
    proxy_index va device_profile'ni foydalanuvchiga DOIMIY biriktirib
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
        proxy_index, proxy = await _next_proxy()
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
                logger.info(f"Kod so'rovi yuborildi: {phone} — proksi orqali ({proxy[1]}, index={proxy_index})")
            else:
                logger.info(f"Kod so'rovi yuborildi: {phone} — proksisiz (to'g'ridan-to'g'ri)")
            return client, result, proxy_index, device

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
    except Exception as e:
        logger.error(f"Error getting folders for user {user_db_id}: {e}")
        return []


async def get_groups_from_folder(user_db_id: int, session_string: str, folder_filter) -> List[Dict]:
    """Extract groups from a dialog filter folder."""
    client = await get_client(user_db_id, session_string)
    if not client:
        return []

    groups = []
    try:
        # Get all dialogs
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


# Import needed for get_dialog_folders
try:
    from telethon.tl.functions.messages import GetDialogFiltersRequest
except ImportError:
    # Fallback for older versions
    class GetDialogFiltersRequest:
        pass