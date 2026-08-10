import asyncio
import logging
import os
from typing import Optional, List, Dict

from telethon import TelegramClient, errors
from telethon.tl.types import (
    DialogFilter, Channel, Chat
)
from telethon.sessions import StringSession

from config import config

logger = logging.getLogger(__name__)

_clients: Dict[int, TelegramClient] = {}


def _get_proxy():
    """Proxy sozlamasini qaytaradi."""
    host = getattr(config, "PROXY_HOST", "") or ""
    port = getattr(config, "PROXY_PORT", 0) or 0
    if host and port:
        return ("socks5", host, int(port))
    return None


async def create_client(user_db_id: int, session_string: str = None) -> TelegramClient:
    if session_string:
        session = StringSession(session_string)
    else:
        session = StringSession()

    proxy = _get_proxy()

    client = TelegramClient(
        session,
        config.API_ID,
        config.API_HASH,
        device_model="PC 64bit",
        system_version="Windows 10",
        app_version="Telegram Desktop",
        lang_code="en",
        system_lang_code="en-US",
        proxy=proxy,
    )
    return client


async def get_client(user_db_id: int, session_string: str) -> Optional[TelegramClient]:
    """Get or restore a client from session string."""
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

    try:
        client = await create_client(user_db_id, session_string)
        await client.connect()
        if await client.is_user_authorized():
            _clients[user_db_id] = client
            return client
    except Exception as e:
        logger.error(f"Failed to restore client for user {user_db_id}: {e}")
    return None


async def send_code(phone: str) -> tuple[TelegramClient, any]:
    """Send login code to phone number."""
    client = await create_client(0)
    await client.connect()
    result = await client.send_code_request(phone)
    return client, result


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
