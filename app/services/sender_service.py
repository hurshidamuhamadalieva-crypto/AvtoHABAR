import asyncio
import logging
import io
from html import escape
from datetime import datetime
from typing import Dict
from app.access import has_access, is_admin

from app.database import (
    get_active_advertisement, get_user_groups,
    get_or_create_settings, update_settings, add_send_stat, get_user_by_db_id, get_active_subscription
)
from app.services.telethon_service import (
    send_message_to_group, SessionRevokedException, AccountBannedException,
    disconnect_client, _clear_user_session, _notify_session_lost,
)

logger = logging.getLogger(__name__)

# Aktiv yuborish tasklari: {user_db_id: asyncio.Task}
_sending_tasks: Dict[int, asyncio.Task] = {}
_wake_events = {}
_media_cache = {}

# Guruhlar orasidagi kutish (spam himoyasi)
DELAY_BETWEEN_GROUPS = 1


async def start_sending(bot, user_db_id: int, telegram_id: int, session_string: str):
    task = _sending_tasks.get(user_db_id)
    if task and not task.done():
        return
    _wake_events[user_db_id] = asyncio.Event()
    _sending_tasks[user_db_id] = asyncio.create_task(
        _sending_loop(bot, user_db_id, telegram_id, session_string),
        name=f"send_{user_db_id}"
    )


async def stop_sending(user_db_id: int):
    task = _sending_tasks.get(user_db_id)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _sending_tasks.pop(user_db_id, None)  # ← KeyError bo'lmaydi
    await update_settings(user_db_id, is_sending=False, sending_paused=False)


async def pause_sending(user_db_id: int):
    await update_settings(user_db_id, sending_paused=True)


async def resume_sending(user_db_id: int):
    await update_settings(user_db_id, sending_paused=False)
    if user_db_id in _wake_events:
        _wake_events[user_db_id].set()


def is_sending(user_db_id: int) -> bool:
    task = _sending_tasks.get(user_db_id)
    return task is not None and not task.done()


async def stop_all_for_user(user_db_id: int):
    await stop_sending(user_db_id)


async def _send_to_entity(client_session: str, user_db_id: int, entity, text: str, photo_file_id=None, bot=None, text_html=None):
    """
    Guruh yoki kanalga xabar yuborish.
    Telethon send_message barcha turdagi entity larni qabul qiladi:
    guruh, supergroup, kanal — farqi yo'q.
    """
    from app.services.telethon_service import get_client
    client = await get_client(user_db_id, client_session)
    if not client:
        return False, "Mijoz ulanmagan"

    from telethon import errors
    for attempt in range(3):
        try:
            body = text_html or escape(text)
            if photo_file_id:
                cached = _media_cache.get(user_db_id)
                if not cached or cached[0] != photo_file_id:
                    stream = io.BytesIO()
                    await bot.download(photo_file_id, destination=stream)
                    stream.seek(0)
                    stream.name = "advertisement.jpg"
                    media = stream
                else:
                    media = cached[1]
                sent = await client.send_file(entity, media, caption=body, parse_mode="html")
                _media_cache[user_db_id] = (photo_file_id, sent.photo)
            else:
                _media_cache.pop(user_db_id, None)
                await client.send_message(entity, body, parse_mode="html")
            return True, ""

        except errors.FloodWaitError as e:
            wait = e.seconds
            logger.warning(f"FloodWait {wait}s — foydalanuvchi {user_db_id}, entity {entity}")
            if wait > 300:
                return False, f"FloodWait: {wait}s (juda uzoq)"
            await asyncio.sleep(wait + 3)

        except errors.FileReferenceExpiredError:
            _media_cache.pop(user_db_id, None)
            if attempt == 2:
                return False, "Rasm havolasi yangilanmadi. Rasmni qayta yuboring."

        except errors.ChatWriteForbiddenError:
            return False, "Yozish taqiqlangan"

        except errors.UserBannedInChannelError:
            return False, "Kanalda bloklangan"

        except errors.SlowModeWaitError as e:
            return False, f"SlowMode: {e.seconds}s"

        except errors.ChannelPrivateError:
            return False, "Kanal/guruh yopiq yoki chiqib ketilgan"

        except errors.UserDeactivatedBanError as e:
            raise AccountBannedException(str(e)) from e
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.AuthKeyDuplicatedError) as e:
            raise SessionRevokedException(str(e)) from e
        except errors.RPCError as e:
            logger.warning(f"RPCError (urinish {attempt+1}/3): {e}")
            if attempt == 2:
                return False, str(e)
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Kutilmagan xato: {e}")
            return False, str(e)

    return False, "3 ta urinishdan keyin ham xato"


async def _sending_loop(bot, user_db_id: int, telegram_id: int, session_string: str):
    """
    Asosiy yuborish sikli:
    1. Barcha guruh/kanallarga ketma-ket yuborish (8s pauza)
    2. Hammaga yuborib bo'lgach → interval kutish
    3. Takrorlash ♻️
    """
    logger.info(f"Yuborish sikli boshlandi: foydalanuvchi {user_db_id}")
    try:
        await update_settings(user_db_id, is_sending=True, sending_paused=False)
        while True:
            if not await _can_send(user_db_id):
                return
            # ── Sozlamalarni olish ────────────────────────────────────────────
            settings = await get_or_create_settings(user_db_id)

            if settings.sending_paused:
                await asyncio.sleep(3)
                continue

            # ── E'lonni olish ─────────────────────────────────────────────────
            ad = await get_active_advertisement(user_db_id)
            if not ad:
                logger.warning(f"[{user_db_id}] E'lon topilmadi, 30s kutilmoqda")
                await asyncio.sleep(30)
                continue

            # ── Guruh/kanallarni olish ────────────────────────────────────────
            groups = await get_user_groups(user_db_id)
            if not groups:
                logger.warning(f"[{user_db_id}] Guruhlar topilmadi, 30s kutilmoqda")
                await asyncio.sleep(30)
                continue

            interval_minutes = settings.interval_minutes or 10
            total = len(groups)
            sent_count = 0
            failed_count = 0

            logger.info(
                f"[{user_db_id}] {total} ta guruh/kanalga yuborilmoqda | "
                f"interval={interval_minutes}daq | orasida {DELAY_BETWEEN_GROUPS}s"
            )

            # ── Hammaga ketma-ket yuborish ────────────────────────────────────
            for idx, group in enumerate(groups, 1):

                # Pauza tekshiruvi
                settings = await get_or_create_settings(user_db_id)
                if settings.sending_paused:
                    logger.info(f"[{user_db_id}] Pauza ({idx}/{total})")
                    while True:
                        await asyncio.sleep(3)
                        settings = await get_or_create_settings(user_db_id)
                        if not settings.sending_paused:
                            logger.info(f"[{user_db_id}] Davom ettirildi")
                            break

                if not await _can_send(user_db_id):
                    return

                # E'lon yangilangan bo'lishi mumkin
                ad = await get_active_advertisement(user_db_id)
                if not ad:
                    logger.warning(f"[{user_db_id}] E'lon o'chirildi, sikl to'xtatildi")
                    break

                # Entity aniqlash: username bo'lsa ishlatamiz, bo'lmasa ID
                from telethon.tl.types import InputPeerChannel, InputPeerChat
                from app.services.telethon_service import target_key
                if group.peer_type == "channel" and group.access_hash is not None:
                    entity = InputPeerChannel(target_key(group.group_id), group.access_hash)
                elif group.peer_type == "chat":
                    entity = InputPeerChat(target_key(group.group_id))
                else:
                    entity = group.group_username or int(group.group_id)

                try:
                    success, error_msg = await _send_to_entity(
                        client_session=session_string,
                        user_db_id=user_db_id,
                        entity=entity,
                        text=ad.text, photo_file_id=ad.photo_file_id, bot=bot, text_html=ad.text_html,
                    )
                except (SessionRevokedException, AccountBannedException) as e:
                    banned = isinstance(e, AccountBannedException)
                    logger.warning(f"[{user_db_id}] Sessiya yo'qoldi (yuborish paytida): {e}")
                    await disconnect_client(user_db_id)
                    await _clear_user_session(telegram_id)
                    await _notify_session_lost(bot, telegram_id, banned)
                    return
                await add_send_stat(
                    user_db_id=user_db_id,
                    group_id=group.group_id,
                    group_title=group.group_title or "",
                    success=success,
                    error_msg=error_msg if not success else None,
                )

                if success:
                    sent_count += 1
                    logger.debug(f"✅ [{idx}/{total}] {group.group_title}")
                else:
                    failed_count += 1
                    logger.warning(f"❌ [{idx}/{total}] {group.group_title}: {error_msg}")

                # Oxirgi guruhdan keyin kutmaslik
                if idx < total:
                    await asyncio.sleep(DELAY_BETWEEN_GROUPS)

            # ── Sikl tugadi ───────────────────────────────────────────────────
            logger.info(
                f"[{user_db_id}] Sikl tugadi → ✅{sent_count} ❌{failed_count} | "
                f"Keyingisi {interval_minutes} daqiqadan keyin"
            )

            wake = _wake_events[user_db_id]
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval_minutes * 60)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    except asyncio.CancelledError:
        logger.info(f"[{user_db_id}] Yuborish bekor qilindi")
        raise
    except Exception as e:
        logger.error(f"[{user_db_id}] Yuborish xatosi: {e}", exc_info=True)
    finally:
        await update_settings(user_db_id, is_sending=False, sending_paused=False)
        _sending_tasks.pop(user_db_id, None)
        _wake_events.pop(user_db_id, None)
        _media_cache.pop(user_db_id, None)
        logger.info(f"[{user_db_id}] Yuborish sikli tugatildi")

async def _can_send(user_db_id):
    user = await get_user_by_db_id(user_db_id)
    if not has_access(user) or not user.session_string:
        return False
    if is_admin(user):
        return True
    sub = await get_active_subscription(user_db_id)
    return not sub or not sub.expires_at or sub.expires_at > datetime.utcnow()


async def shutdown_senders():
    await asyncio.gather(*(stop_sending(uid) for uid in list(_sending_tasks)))
