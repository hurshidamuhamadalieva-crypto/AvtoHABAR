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
from app.services import sender_service as sender, telethon_service as tg
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


    async def test_direct_client_ignores_legacy_proxy(self):
        with patch.object(tg, 'TelegramClient') as factory, patch.object(tg.config, 'API_ID', 123):
            await tg.create_client(0, proxy=('socks5', 'unused', 1))
            self.assertIsNone(factory.call_args.kwargs['proxy'])
            self.assertEqual(factory.call_args.kwargs['device_model'], 'AvtoHABAR')

    async def test_direct_code_response_and_cleanup_on_unsupported_delivery(self):
        from telethon.tl.types import auth
        result = auth.SentCode(auth.SentCodeTypeApp(5), 'hash')
        client = NS(send_code_request=AsyncMock(return_value=result), disconnect=AsyncMock())
        with patch.object(tg, 'connect_login_client', AsyncMock(return_value=client)), patch.object(tg, '_last_send_code_at', 0):
            returned, response, proxy, profile = await tg.send_code('+998000000000')
            self.assertIs(returned, client)
            self.assertIsNone(proxy)
            self.assertIn('Telegram xizmat chatiga', tg.code_delivery(response))
        client.send_code_request.return_value = auth.SentCode(auth.SentCodeTypeSetUpEmailRequired(), 'hash')
        with patch.object(tg, 'connect_login_client', AsyncMock(return_value=client)), patch.object(tg, '_last_send_code_at', 0):
            with self.assertRaisesRegex(tg.DirectConnectionException, 'QR'):
                await tg.send_code('+998000000000')
        client.disconnect.assert_awaited_once()

    async def test_admin_has_access_without_payment_and_bypasses_expired_plan(self):
        from app.access import has_access
        user = NS(telegram_id=5555, is_active=False, is_banned=False, session_string='session')
        with patch.object(db.config, 'ADMIN_IDS', [5555]), patch.object(sender, 'get_user_by_db_id', AsyncMock(return_value=user)), patch.object(sender, 'get_active_subscription', AsyncMock()) as sub:
            self.assertTrue(has_access(user))
            self.assertTrue(bc._check_active(user))
            self.assertTrue(groups._check_active(user))
            self.assertTrue(await sender._can_send(1))
            sub.assert_not_awaited()

    async def test_admin_start_opens_menu_instead_of_plans(self):
        from app.handlers import start
        user = NS(telegram_id=5555, is_active=False, is_banned=False, session_string=None)
        message = NS(from_user=NS(id=5555, username='admin', full_name='Admin'), answer=AsyncMock())
        state = NS(clear=AsyncMock())
        with patch.object(db.config, 'ADMIN_IDS', [5555]), patch.object(start, 'get_or_create_user', AsyncMock(return_value=user)):
            await start.cmd_start(message, state)
        callbacks = [b.callback_data for row in message.answer.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertIn('menu:admin', callbacks)
        self.assertNotIn('plan:1month', callbacks)

    async def test_qr_png_generated_locally(self):
        from app.handlers.phone import _qr_image
        png = _qr_image('tg://login?token=test-only')
        self.assertTrue(png.startswith(bytes([137,80,78,71,13,10,26,10])))
        self.assertGreater(len(png), 100)

    async def test_two_factor_does_not_resubmit_consumed_code(self):
        client = NS(sign_in=AsyncMock(), session=NS(save=lambda: 'session'))
        self.assertEqual(await tg.sign_in(client, 'phone', 'old', 'hash', password='pass'), 'session')
        client.sign_in.assert_awaited_once_with(password='pass')

    async def test_qr_completion_saves_direct_session(self):
        from app.handlers import phone
        client = NS(get_me=AsyncMock(return_value=NS(phone='998000000000', bot=False)), session=NS(save=lambda: 'qr-session'), disconnect=AsyncMock())
        pending = {'client': client, 'qr': True, 'qr_message': NS(delete=AsyncMock())}
        phone._pending_logins[77] = pending
        call = NS(from_user=NS(id=77, username='driver', full_name='Driver'), message=NS(answer=AsyncMock()))
        state = NS(clear=AsyncMock())
        with patch.object(phone, 'get_or_create_user', AsyncMock(return_value=NS(is_admin=False))), patch.object(phone, 'update_user', AsyncMock()) as update:
            await phone._wait_qr(call, state, pending, NS(wait=AsyncMock()))
            self.assertEqual(update.call_args.kwargs['session_string'], 'qr-session')
            self.assertIsNone(update.call_args.kwargs['proxy_key'])
        self.assertNotIn(77, phone._pending_logins)
        client.disconnect.assert_awaited_once()
        pending['qr_message'].delete.assert_awaited_once()

    async def test_qr_two_factor_preserves_client_for_password(self):
        from app.handlers import phone
        from telethon.errors import SessionPasswordNeededError
        client = NS(disconnect=AsyncMock())
        pending = {'client': client, 'qr': True, 'qr_message': NS(delete=AsyncMock())}
        phone._pending_logins[78] = pending
        call = NS(from_user=NS(id=78), message=NS(answer=AsyncMock()))
        state = NS(set_state=AsyncMock())
        await phone._wait_qr(call, state, pending, NS(wait=AsyncMock(side_effect=SessionPasswordNeededError(None))))
        client.disconnect.assert_not_awaited()
        state.set_state.assert_awaited_once_with(phone.PhoneFlow.entering_password)
        await phone._disconnect_pending(78)

    async def test_qr_expiry_closes_client_and_clears_state(self):
        from app.handlers import phone
        client = NS(disconnect=AsyncMock())
        pending = {'client': client, 'qr': True, 'qr_message': NS(delete=AsyncMock())}
        phone._pending_logins[79] = pending
        call = NS(from_user=NS(id=79), message=NS(answer=AsyncMock()))
        state = NS(clear=AsyncMock())
        await phone._wait_qr(call, state, pending, NS(wait=AsyncMock(side_effect=asyncio.TimeoutError)))
        client.disconnect.assert_awaited_once()
        self.assertNotIn(79, phone._pending_logins)
        state.clear.assert_awaited_once()

    async def test_resend_respects_telegram_cooldown(self):
        from app.handlers import phone
        import time
        phone._pending_logins[80] = {'can_resend': True, 'resend_used': False, 'resend_at': time.monotonic()+100}
        call = NS(from_user=NS(id=80), answer=AsyncMock())
        with patch.object(tg, 'resend_code', AsyncMock()) as resend:
            await phone.cb_resend_code(call, NS())
            resend.assert_not_awaited()
        phone._pending_logins.pop(80)

    async def test_existing_user_becomes_admin_without_payment(self):
        await db.get_or_create_user(5556)
        with patch.object(db.config, 'ADMIN_IDS', [5556]):
            user = await db.get_or_create_user(5556)
            self.assertTrue(user.is_admin)
            from app.access import has_access
            self.assertTrue(has_access(user))
            self.assertEqual(await db.get_user_payments(user.id), [])

    async def test_missing_api_credentials_reports_configuration_problem(self):
        with patch.object(tg.config, 'API_ID', 0):
            with self.assertRaisesRegex(tg.DirectConnectionException, 'API_ID'):
                await tg.connect_login_client()

    async def test_old_payment_button_does_not_charge_admin(self):
        from app.handlers import payment
        call = NS(from_user=NS(id=5555), message=NS(answer=AsyncMock()), answer=AsyncMock())
        state = NS(clear=AsyncMock())
        with patch.object(db.config, 'ADMIN_IDS', [5555]), patch.object(payment, 'create_payment', AsyncMock()) as create:
            await payment.cb_pay_card(call, state)
            create.assert_not_awaited()
        self.assertIn("Admin", call.message.answer.call_args.args[0])

    async def test_buttons(self):
        callbacks = lambda kb: [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn('bc_photo', callbacks(kb_broadcast_menu(False, True)))
        self.assertIn('bc_edit', callbacks(kb_broadcast_sending(True)))
        self.assertNotIn('bc_edit', callbacks(kb_broadcast_sending(False)))

if __name__ == '__main__':
    unittest.main()
