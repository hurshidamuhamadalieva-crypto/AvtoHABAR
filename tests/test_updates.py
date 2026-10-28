import asyncio
import os
import tempfile
from pathlib import Path
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

_tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + _tmp.name.replace('\\', '/') + '/test.db'
from app import database as db
from app.services import sender_service as sender, telethon_service as tg, proxy_health
from app.handlers import broadcast as bc, groups
from app.keyboards import kb_broadcast_menu, kb_broadcast_sending
from telethon.tl.types import Channel, ChatAdminRights


class Updates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()
        sender._media_cache.clear()

    async def test_photo_saved_and_old_ad_removed(self):
        user = await db.get_or_create_user(123)
        await db.save_advertisement(user.id, 'old')
        await db.save_advertisement(user.id, 'new', 'photo-id', '<b>new</b>')
        ad = await db.get_active_advertisement(user.id)
        self.assertEqual((ad.photo_file_id, ad.text_html), ('photo-id', '<b>new</b>'))
        async with db.async_session_maker() as session:
            self.assertEqual(len((await session.execute(db.select(db.Advertisement).where(db.Advertisement.user_id == user.id))).scalars().all()), 1)

    async def test_channel_rights_and_canonical_id(self):
        channel = Channel(id=99, title='Test', photo=None, date=None, broadcast=True, access_hash=55)
        self.assertFalse(tg.is_send_target(channel))
        channel.admin_rights = ChatAdminRights(post_messages=True)
        self.assertTrue(tg.is_send_target(channel))
        record = tg.target_record(channel)
        self.assertEqual(record['id'], -1000000000099)
        self.assertEqual(tg.target_key(record['id']), 99)
        self.assertEqual(record['access_hash'], 55)

    async def test_photo_download_once_and_reuse(self):
        client = NS(send_file=AsyncMock(return_value=NS(photo='uploaded')), send_message=AsyncMock())
        bot = NS(download=AsyncMock())
        with patch.object(tg, 'get_client', AsyncMock(return_value=client)):
            for _ in range(2):
                self.assertEqual(await sender._send_to_entity('s', 888, 1, 'hello', 'photo', bot), (True, ''))
        self.assertEqual(bot.download.await_count, 1)
        self.assertEqual(client.send_file.await_count, 2)
        self.assertEqual(client.send_file.call_args.args[1], 'uploaded')

    async def test_expired_photo_reference_downloads_again(self):
        from telethon.errors import FileReferenceExpiredError
        sender._media_cache[889] = ('photo', 'expired')
        client = NS(send_file=AsyncMock(side_effect=[FileReferenceExpiredError(None), NS(photo='fresh')]))
        bot = NS(download=AsyncMock())
        with patch.object(tg, 'get_client', AsyncMock(return_value=client)):
            self.assertEqual(await sender._send_to_entity('s', 889, 1, 'hello', 'photo', bot), (True, ''))
        bot.download.assert_awaited_once()
        self.assertEqual(sender._media_cache[889], ('photo', 'fresh'))

    async def test_edit_preserves_interval_and_resumes(self):
        state = NS(get_data=AsyncMock(return_value={'editing': True, 'photo_file_id': 'photo'}), clear=AsyncMock())
        message = NS(text='new', html_text='<b>new</b>', from_user=NS(id=1), answer=AsyncMock())
        with patch.object(bc, 'get_or_create_user', AsyncMock(return_value=NS(id=1))), patch.object(bc, 'get_or_create_settings', AsyncMock(return_value=NS(sending_paused=True, interval_minutes=7))), patch.object(bc, 'save_advertisement', AsyncMock()) as save, patch.object(sender, 'is_sending', return_value=True), patch.object(sender, 'resume_sending', AsyncMock()) as resume:
            await bc.receive_ad_text(message, state)
            save.assert_awaited_once_with(1, 'new', 'photo', '<b>new</b>')
            resume.assert_awaited_once_with(1)
            self.assertIn('7', message.answer.call_args.args[0])

    async def test_empty_selection_clears_saved_targets(self):
        call = NS(from_user=NS(id=72), message=NS(edit_text=AsyncMock()), answer=AsyncMock())
        with patch.object(groups, 'get_or_create_user', AsyncMock(return_value=NS(id=72))), patch.object(groups, 'save_groups', AsyncMock()) as save:
            await groups.cb_group_picker_done(call, NS())
            save.assert_awaited_once_with(72, [], tg.BOT_FOLDER_TITLE)

    async def test_proxy_counts_and_cache(self):
        proxy_health._cached = None
        with patch.object(proxy_health.config, 'PROXIES', [('x',)] * 3), patch.object(proxy_health, '_probe', side_effect=[True, False, True]) as probe:
            report = await proxy_health.check_proxies()
            self.assertEqual({k: report[k] for k in ('total', 'working', 'failed')}, {'total': 3, 'working': 2, 'failed': 1})
            self.assertEqual(await proxy_health.check_proxies(), report)
            self.assertEqual(probe.call_count, 3)
        proxy_health._cached = None

    async def test_history_cleanup_preserves_total_counter(self):
        user = await db.get_or_create_user(234)
        await db.get_or_create_settings(user.id)
        await db.add_send_stat(user.id, 1, 'test', True)
        async with db.async_session_maker() as session:
            await session.execute(db.update(db.SendStat).where(db.SendStat.user_id == user.id).values(sent_at=datetime.utcnow()-timedelta(days=100)))
            await session.commit()
        await db.cleanup_history()
        settings = await db.get_or_create_settings(user.id)
        self.assertEqual(settings.messages_sent, 1)
        async with db.async_session_maker() as session:
            self.assertEqual(len((await session.execute(db.select(db.SendStat).where(db.SendStat.user_id == user.id))).scalars().all()), 0)

    async def test_migration_of_old_database(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine('sqlite+aiosqlite:///' + _tmp.name.replace('\\', '/') + '/legacy.db')
        async with engine.begin() as conn:
            await conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            await conn.exec_driver_sql("CREATE TABLE advertisements (id INTEGER PRIMARY KEY, text TEXT)")
            await conn.exec_driver_sql("INSERT INTO advertisements VALUES (1, 'existing')")
            await conn.exec_driver_sql("CREATE TABLE groups (id INTEGER PRIMARY KEY)")
            await db._ensure_columns(conn)
            await db._ensure_columns(conn)
            columns = {r[1] for r in (await conn.exec_driver_sql('PRAGMA table_info(advertisements)')).fetchall()}
            self.assertIn('photo_file_id', columns)
            self.assertEqual((await conn.exec_driver_sql('SELECT text FROM advertisements')).scalar(), 'existing')
        await engine.dispose()

    async def test_expired_subscription_prevents_sending(self):
        with patch.object(sender, 'get_user_by_db_id', AsyncMock(return_value=NS(is_active=True, is_banned=False, session_string='session'))), patch.object(sender, 'get_active_subscription', AsyncMock(return_value=NS(expires_at=datetime.utcnow()-timedelta(seconds=1)))):
            self.assertFalse(await sender._can_send(999))

    async def test_photo_flow_from_button_to_sender(self):
        data = {}
        async def update_data(**kwargs):
            data.update(kwargs)
        async def clear():
            data.clear()
        state = NS(clear=AsyncMock(side_effect=clear), update_data=AsyncMock(side_effect=update_data),
                   get_data=AsyncMock(side_effect=lambda: dict(data)), set_state=AsyncMock())
        target = NS(edit_text=AsyncMock())
        call = NS(from_user=NS(id=7), message=target, answer=AsyncMock(), bot=NS(), data='bc_interval:7')
        photo = NS(photo=[NS(file_id='new-photo')], answer=AsyncMock())
        text = NS(text='hello & welcome', html_text='hello &amp; welcome', answer=AsyncMock())
        await bc.cb_photo(call, state)
        await bc.receive_ad_photo(photo, state)
        await bc.receive_ad_text(text, state)
        await bc.cb_choose_interval(call, state)
        self.assertEqual(data['interval'], 7)
        self.assertEqual(data['photo_file_id'], 'new-photo')
        self.assertIn('Rasmli', target.edit_text.call_args.args[0])
        with patch.object(bc, 'get_or_create_user', AsyncMock(return_value=NS(id=7, session_string='session'))), patch.object(bc, 'get_user_groups', AsyncMock(return_value=[NS()])), patch.object(bc, 'save_advertisement', AsyncMock()) as save, patch.object(bc, 'update_settings', AsyncMock()) as settings, patch.object(sender, 'start_sending', AsyncMock()) as start:
            await bc.cb_confirm_broadcast(call, state)
            save.assert_awaited_once_with(7, 'hello & welcome', 'new-photo', 'hello &amp; welcome')
            settings.assert_awaited_once_with(7, interval_minutes=7)
            start.assert_awaited_once_with(call.bot, 7, 7, 'session')

    async def test_proxy_parser_preserves_passwords_and_supports_urls(self):
        from config import parse_proxies
        proxies, invalid = parse_proxies('host:1080:user:pass:with:colons, http://name:p%40ss@host2:8080;invalid')
        self.assertEqual(len(proxies), 2)
        self.assertEqual(invalid, 1)
        self.assertEqual(proxies[0][4:], ('user', 'pass:with:colons'))
        self.assertEqual(proxies[1][0], 'http')
        self.assertEqual(proxies[1][4:], ('name', 'p@ss'))

    async def test_proxy_probe_reports_auth_failure_without_retry(self):
        import socks
        with patch.object(proxy_health, '_tunnel', side_effect=socks.GeneralProxyError('failed', socks.SOCKS5AuthError('failed'))) as tunnel:
            self.assertEqual(proxy_health._probe(('socks5', 'host', 1, True, 'user', 'password')), 'auth_error')
            self.assertEqual(tunnel.call_count, 1)

    async def test_login_stops_after_repeated_auth_failures(self):
        import socks
        client = NS(connect=AsyncMock(side_effect=socks.GeneralProxyError('failed', socks.SOCKS5AuthError('failed'))), disconnect=AsyncMock(), send_code_request=AsyncMock())
        proxies = [('socks5', 'host', n, True, 'user', 'pass') for n in range(10)]
        with patch.object(tg.config, 'PROXIES', proxies), patch.object(tg, '_last_send_code_at', 0), patch.object(tg, 'create_client', AsyncMock(return_value=client)), patch.object(proxy_health, 'cached_status', return_value=None):
            with self.assertRaisesRegex(tg.ProxyConnectionException, 'login/paroli'):
                await tg.send_code('+998000000000')
        self.assertEqual(client.connect.await_count, 3)
        self.assertEqual(client.disconnect.await_count, 3)
        client.send_code_request.assert_not_awaited()

    async def test_admin_grant_creates_plan_without_payment_and_is_idempotent(self):
        with patch.object(db.config, 'ADMIN_IDS', [987]):
            self.assertTrue(await db.grant_driver_subscription(98765, '3months', 987, 'grant-test-token'))
            self.assertFalse(await db.grant_driver_subscription(98765, '3months', 987, 'grant-test-token'))
        user = await db.get_user(98765)
        self.assertTrue(user.is_active)
        sub = await db.get_active_subscription(user.id)
        self.assertEqual(sub.days, 90)
        self.assertEqual(sub.plan_key, '3months')
        self.assertEqual(await db.get_user_payments(user.id), [])
        with patch.object(db.config, 'ADMIN_IDS', [987]):
            with self.assertRaises(ValueError):
                await db.grant_driver_subscription(98766, '1month', 123, 'unauthorized-token')
        self.assertIsNone(await db.get_user(98766))

    async def test_admin_driver_flow_requires_plan_and_confirmation(self):
        from app.handlers import admin
        data = {}
        async def update_data(**kwargs):
            data.update(kwargs)
        async def clear():
            data.clear()
        state = NS(clear=AsyncMock(side_effect=clear), update_data=AsyncMock(side_effect=update_data), get_data=AsyncMock(side_effect=lambda: dict(data)), set_state=AsyncMock())
        call = NS(from_user=NS(id=987), message=NS(edit_text=AsyncMock()), answer=AsyncMock(), bot=NS(send_message=AsyncMock()), data='driver_plan:1month')
        message = NS(text='98767', answer=AsyncMock())
        await admin.cb_add_driver(call, state)
        await admin.receive_driver_id(message, state)
        await admin.choose_driver_plan(call, state)
        self.assertIsNone(await db.get_user(98767))
        self.assertEqual(data['driver_plan'], '1month')
        call.data = 'driver_confirm:' + data['grant_key']
        with patch.object(db.config, 'ADMIN_IDS', [987]):
            await admin.confirm_driver(call, state)
        self.assertTrue((await db.get_user(98767)).is_active)
        call.bot.send_message.assert_awaited_once()

    async def test_old_admin_confirmation_cannot_grant_new_driver(self):
        from app.handlers import admin
        state = NS(get_data=AsyncMock(return_value={'driver_id': 100, 'driver_plan': '1month', 'grant_key': 'new-token'}))
        call = NS(data='driver_confirm:old-token', answer=AsyncMock())
        with patch.object(db, 'grant_driver_subscription', AsyncMock()) as grant:
            await admin.confirm_driver(call, state)
            grant.assert_not_awaited()
        self.assertTrue(call.answer.call_args.kwargs['show_alert'])

    async def test_login_prefers_verified_working_proxy(self):
        proxies = [('socks5', 'host', n, True, 'u', 'p') for n in (1, 2)]
        with patch.object(tg.config, 'PROXIES', proxies), patch.object(proxy_health, 'cached_status', side_effect=lambda p: 'working' if p[2] == 2 else 'timeout'):
            _, chosen = await tg._next_proxy()
            self.assertEqual(chosen[2], 2)

    async def test_buttons(self):
        callbacks = lambda kb: [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn('bc_photo', callbacks(kb_broadcast_menu(False, True)))
        self.assertIn('bc_edit', callbacks(kb_broadcast_sending(True)))
        self.assertNotIn('bc_edit', callbacks(kb_broadcast_sending(False)))

if __name__ == '__main__':
    unittest.main()
