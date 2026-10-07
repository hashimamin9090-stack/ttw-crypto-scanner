import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import TTW_BOT_V2 as infra
from ttw_v3 import Scanner


class SharingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = Scanner.__new__(Scanner)
        self.bot.chat_id = '1'
        self.bot.store = infra.StateStore(self.temp.name)
        self.bot.stats = Counter()
        self.bot.status_text = lambda: 'online'
        self.calls = []

        async def call(method, payload):
            self.calls.append((method, payload))
            if method == 'getMe':
                return {'result': {'username': 'testbot'}}
            return {'result': {'message_id': int(payload.get('chat_id', 1)) * 10}}

        self.bot.telegram_call = call

    async def message(self, chat, text, kind='private'):
        await self.bot.handle_message({'chat': {'id': chat, 'type': kind}, 'text': text})

    async def test_invite_private_single_use_persists_and_owner_is_retained(self):
        await self.message(2, '/invite')
        self.assertFalse(self.bot.store.data['telegram_invites'])
        await self.message(1, '/invite')
        token = next(iter(self.bot.store.data['telegram_invites']))
        await self.message(-2, '/start ' + token, 'group')
        self.assertEqual(self.bot.recipient_ids(), ['1'])
        await self.message(2, '/start ' + token)
        await self.message(3, '/start ' + token)
        self.assertEqual(self.bot.recipient_ids(), ['1', '2'])
        reloaded = infra.StateStore(self.temp.name)
        self.assertIn('2', reloaded.data['telegram_subscribers'])
        await self.message(2, '/stop')
        self.assertEqual(self.bot.recipient_ids(), ['1'])

    async def test_expired_and_plain_start_do_not_subscribe(self):
        self.bot.store.data['telegram_invites'] = {'expired': 1}
        await self.message(2, '/start expired')
        await self.message(2, '/start')
        self.assertEqual(self.bot.recipient_ids(), ['1'])

    async def test_broadcast_saves_each_message_and_edits_each_copy(self):
        self.bot.store.data['telegram_subscribers'] = {'2': {}}
        self.bot.store.reserve('key', {'setup': {}, 'delivery_kind': 'text'})
        await self.bot.broadcast_alert('key', 'alert', {})
        record = self.bot.store.data['alerts']['key']
        self.assertEqual(record['deliveries'], {'1': {'message_id': 10}, '2': {'message_id': 20}})
        self.assertEqual(record['message_id'], 10)
        with patch('ttw_v3.alert_payload', return_value=('invalid', {})):
            await self.bot.edit_record('key', record, 'INVALID')
            await self.bot.edit_record('key', record, 'INVALID')
        edits = [p for m, p in self.calls if m == 'editMessageText']
        self.assertEqual([(p['chat_id'], p['message_id']) for p in edits], [('1', 10), ('2', 20)])

    async def test_failed_owner_does_not_block_friend(self):
        self.bot.store.data['telegram_subscribers'] = {'2': {}}
        self.bot.store.reserve('key', {'setup': {}})
        original = self.bot.telegram_call
        async def call(method, payload):
            if payload['chat_id'] == '1':
                raise RuntimeError('blocked')
            return await original(method, payload)
        self.bot.telegram_call = call
        await self.bot.broadcast_alert('key', 'alert', {})
        record = self.bot.store.data['alerts']['key']
        self.assertEqual(record['status'], 'sent')
        self.assertEqual(record['deliveries'], {'2': {'message_id': 20}})

    async def test_photo_delivery_and_owner_removal_control(self):
        self.bot.store.data['telegram_subscribers'] = {'2': {}}
        self.bot.store.reserve('key', {'setup': {}, 'delivery_kind': 'photo'})
        async def photo(payload, png):
            self.assertEqual(png, b'png')
            return await self.bot.telegram_call('sendPhoto', payload)
        self.bot.telegram_photo = photo
        await self.bot.broadcast_alert('key', 'alert', {}, b'png')
        await self.message(2, '/remove 2')
        self.assertIn('2', self.bot.recipient_ids())
        await self.message(1, '/remove 2')
        self.assertEqual(self.bot.recipient_ids(), ['1'])

    async def test_legacy_edit_stays_with_owner(self):
        record = {'setup': {}, 'message_id': 99}
        with patch('ttw_v3.alert_payload', return_value=('invalid', {})):
            await self.bot.edit_record('key', record, 'INVALID')
        self.assertEqual(self.calls[-1][1]['chat_id'], '1')


if __name__ == '__main__':
    unittest.main()
