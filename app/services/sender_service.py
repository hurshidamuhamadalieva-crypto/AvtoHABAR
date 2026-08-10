import asyncio
import logging
from datetime import datetime
from typing import Dict

from app.database import (
    get_active_advertisement, get_user_groups,
    get_or_create_settings, update_settings, add_send_stat
)
from app.services.telethon_service import send_message_to_group

logger = logging.getLogger(__name__)

# Aktiv yuborish tasklari: {user_db_id: asyncio.Task}
_sending_tasks: Dict[int, asyncio.Task] = {}

# Guruhlar orasidagi kutish (spam himoyasi)
DELAY_BETWEEN_GROUPS = 8


async def start_sending(bot, user_db_id: int, telegram_id: int, session_string: str):
    task = _sending_tasks.get(user_db_id)
    if task and not task.done():
        return
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


def is_sending(user_db_id: int) -> bool:
    task = _sending_tasks.get(user_db_id)
    return task is not None and not task.done()


async def stop_all_for_user(user_db_id: int):
    await stop_sending(user_db_id)


async def _send_to_entity(client_session: str, user_db_id: int, entity, text: str):
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
            await client.send_message(entity, text)
            return True, ""

        except errors.FloodWaitError as e:
            wait = e.seconds
            logger.warning(f"FloodWait {wait}s — foydalanuvchi {user_db_id}, entity {entity}")
            if wait > 300:
                return False, f"FloodWait: {wait}s (juda uzoq)"
            await asyncio.sleep(wait + 3)

        except errors.ChatWriteForbiddenError:
            return False, "Yozish taqiqlangan"

        except errors.UserBannedInChannelError:
            return False, "Kanalda bloklangan"

        except errors.SlowModeWaitError as e:
            return False, f"SlowMode: {e.seconds}s"

        except errors.ChannelPrivateError:
            return False, "Kanal/guruh yopiq yoki chiqib ketilgan"

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
    await update_settings(user_db_id, is_sending=True, sending_paused=False)

    try:
        while True:
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

                # E'lon yangilangan bo'lishi mumkin
                ad = await get_active_advertisement(user_db_id)
                if not ad:
                    logger.warning(f"[{user_db_id}] E'lon o'chirildi, sikl to'xtatildi")
                    break

                # Entity aniqlash: username bo'lsa ishlatamiz, bo'lmasa ID
                entity = group.group_username if group.group_username else int(group.group_id)

                success, error_msg = await _send_to_entity(
                    client_session=session_string,
                    user_db_id=user_db_id,
                    entity=entity,
                    text=ad.text,
                )

                await add_send_stat(
                    user_db_id=user_db_id,
                    group_id=group.group_id,
                    group_title=group.group_title or "",
                    success=success,
                    error_msg=error_msg if not success else None,
                )

                if success:
                    sent_count += 1
                    logger.info(f"✅ [{idx}/{total}] {group.group_title}")
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

            await asyncio.sleep(interval_minutes * 60)

    except asyncio.CancelledError:
        logger.info(f"[{user_db_id}] Yuborish bekor qilindi")
        raise
    except Exception as e:
        logger.error(f"[{user_db_id}] Yuborish xatosi: {e}", exc_info=True)
    finally:
        await update_settings(user_db_id, is_sending=False, sending_paused=False)
        _sending_tasks.pop(user_db_id, None)  # ← KeyError bo'lmaydi
        logger.info(f"[{user_db_id}] Yuborish sikli tugatildi")