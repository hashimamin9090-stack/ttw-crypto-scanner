"""Replay the recorded BTC 3H decision; trade-stream timing is not recreated."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ttw_context as context
import ttw_v3 as bot
from futures_feed import FuturesScanner
from test_ttw_v352 import scanner


def recorded_watch(bull=False):
    data = json.loads((Path(__file__).parent/'fixtures/btc_3h_20261021.json').read_text())
    def candles(field):
        bars = [bot.Candle(*row) for row in data[field]]
        if bull:
            # Reflected prices test the other direction, not another live event.
            bars = [replace(b, open=170000-b.open, high=170000-b.low,
                            low=170000-b.high, close=170000-b.close) for b in bars]
        return bars
    bars = candles('bars')
    direction = 'BULLISH' if bull else 'BEARISH'
    plan = bot.make_plan(bars[-3:], direction, .01, .25, .15)
    key = f'BTCUSDT|3H|{direction}|{bars[-1].open_time}'
    watch = bot.Watch(key, 'BTCUSDT', '3H', bars, plan)
    # This state passed the live trigger before the recorded quality rejection.
    # Reusing it tests downstream eligibility, not historical verification time.
    watch.verified_ms = watch.last_ms = data['now_ms']
    watch.touch_ms = data['touch_ms']
    watch.extreme = bars[-1].low if bull else bars[-1].high
    watch.post_high, watch.post_low = bars[-1].high, bars[-1].low
    watch.last_price = bars[-1].close
    watch.parent_bars = candles('parent_bars')
    return watch, {'15m':candles('btc_short'), '1h':candles('btc_hours')}


def seed(scanner, watch, btc):
    scanner.btc_bars = btc
    scanner.last_quotes['BTCUSDT'] = (watch.last_ms, watch.last_price)


class BTCEntryPolicy(unittest.TestCase):
    def test_only_enabled_spot_btc_hourly_scans_make_location_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            for tf in bot.TIMEFRAMES:
                for symbol in ('BTCUSDT', 'ETHUSDT', 'NEARUSDT'):
                    with self.subTest(tf=tf, symbol=symbol):
                        default = context.timeframe_profile(tf)
                        actual = s.entry_profile(symbol, tf)
                        if symbol == 'BTCUSDT' and tf.endswith('H'):
                            expected = dict(default, require_location=False,
                                            location_policy='BTC_INTRADAY_CONTEXT_ONLY')
                            self.assertEqual(actual, expected)
                        else:
                            self.assertEqual(actual, default)
                        self.assertEqual(context.timeframe_profile(tf), default)
            futures = scanner(folder, FuturesScanner)
            self.assertTrue(futures.entry_profile('BTCUSDT', '3H')['require_location'])
            self.assertFalse(futures.entry_profile('BTCUSDT', '3H')['btc_expansion_veto'])
            s.cfg.quality_enabled = False
            self.assertEqual(s.entry_profile('BTCUSDT', '3H'), context.timeframe_profile('3H'))
            with self.assertRaises(ValueError):
                s.entry_profile('BTCUSDT', 'badH')

    def test_recorded_decision_changes_from_missing_level_to_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            w, btc = recorded_watch()
            seed(s, w, btc)
            setup = bot.make_setup(w, .1, c2_reference=True)
            self.assertEqual([setup[k] for k in ('wick1', 'wick2', 'wick3')],
                             [84898, 84876.01, 84847.31])
            self.assertTrue(w.ready(w.last_ms, s.entry_move_limit(w)))
            # The old generic intraday policy reproduces the live veto.
            with patch.object(s, 'entry_profile', return_value=context.timeframe_profile('3H')):
                before = s.quality_check(w, setup, w.last_ms)
            self.assertEqual(before['reason'], 'NO_ESTABLISHED_LEVEL')
            self.assertAlmostEqual(before['min_reclaim'], 27.8368)
            self.assertAlmostEqual(before['body_reclaim'], 28.02)
            self.assertAlmostEqual(before['location']['setup_atr'], 745.3428571428542)
            after = s.quality_check(w, setup, w.last_ms)
            self.assertTrue(after['ok'], after)
            self.assertEqual(after['reason'], 'QUALITY_READY')
            self.assertFalse(after['location']['ok'])
            self.assertIsNone(after['location']['level'])
            self.assertEqual(after['location']['obstacle']['level'], 82635.32)
            self.assertEqual(after['target_plan']['target_mode'], 'STRUCTURE')
            self.assertLess(after['target_plan']['tp1'], setup['entry'])
            self.assertGreater(after['target_plan']['tp1'], 82635.32)
            self.assertEqual(set(after['risk_checks']), {'sl1', 'sl2'})

    def test_missing_location_does_not_bypass_reclaim_btc_or_both_stop_risk(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            w, btc = recorded_watch()
            seed(s, w, btc)
            setup = bot.make_setup(w, .1, c2_reference=True)
            small = dict(setup, entry=w.plan.candle3_open-5.97)
            self.assertEqual(s.quality_check(w, small, w.last_ms)['reason'], 'RECLAIM_TOO_SMALL')
            for state, reason in (({'ok':False, 'regime':'UNKNOWN'}, 'BTC_DATA_UNAVAILABLE'),
                                  ({'ok':True, 'regime':'UP_EXPANSION'}, 'BTC_OPPOSING_EXPANSION')):
                with patch.object(bot, 'btc_context', return_value=state):
                    self.assertEqual(s.quality_check(w, setup, w.last_ms)['reason'], reason)
            self.assertEqual(s.quality_check(w, dict(setup, sl2=90000), w.last_ms)['reason'],
                             'C2_TARGET_TOO_SMALL_FOR_RISK')
            location = context.location_context(w.bars, w.parent_bars, 'BEARISH',
                setup['entry'], setup['wick3'], .01, strong=True, interaction=True)
            location = deepcopy(location)
            location['obstacle'] = dict(level=setup['entry']-1, distance=1,
                                        kind='SWING_LOW', source='SETUP', known_at_ms=0)
            with patch.object(context, 'location_context', return_value=location):
                self.assertEqual(s.quality_check(w, setup, w.last_ms)['reason'],
                                 'OPPOSING_LEVEL_TOO_CLOSE')

    def test_recorded_no_level_decision_stays_blocked_for_other_markets(self):
        for cls, symbol in ((bot.Scanner, 'ETHUSDT'), (FuturesScanner, 'BTCUSDT')):
            with self.subTest(market=cls.market_name, symbol=symbol), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder, cls)
                w, btc = recorded_watch()
                seed(s, w, btc)
                w.symbol = symbol
                setup = dict(bot.make_setup(w, .1, c2_reference=True), symbol=symbol)
                self.assertEqual(s.quality_check(w, setup, w.last_ms)['reason'], 'NO_ESTABLISHED_LEVEL')


class BTCEntryDelivery(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, s, w):
        calls = []
        async def telegram(method, payload):
            calls.append((method, payload))
            return {'ok':True, 'result':{'message_id':123}}
        s.telegram_call = telegram
        with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
            await s.send_watch(w)
        return calls

    async def test_recorded_decision_delivers_once_without_old_level_both_directions(self):
        for bull in (False, True):
            with self.subTest(bull=bull), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder)
                w, btc = recorded_watch(bull)
                seed(s, w, btc)
                self.assertTrue(w.ready(w.last_ms, s.entry_move_limit(w)))
                self.assertEqual(len(await self.deliver(s, w)), 1, dict(s.stats))
                record = s.store.data['alerts'][w.key]
                self.assertTrue(record['entry_quality']['ok'])
                self.assertFalse(record['entry_quality']['location']['ok'])
                self.assertEqual(record['entry_quality']['profile']['location_policy'],
                                 'BTC_INTRADAY_CONTEXT_ONLY')
                self.assertEqual(record['setup']['stop2_basis'], 'C2_WICK')
                self.assertEqual(record['setup']['target_mode'], 'STRUCTURE')
                self.assertEqual(await self.deliver(s, w), [])

    async def test_missing_level_policy_still_needs_verification_colour_and_early_entry(self):
        for case in ('unverified', 'wrong_colour', 'late_entry', 'stale_trade'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder)
                w, btc = recorded_watch()
                seed(s, w, btc)
                now = w.last_ms
                if case == 'unverified':
                    w.verified_ms = 0
                elif case == 'wrong_colour':
                    w.observe(w.plan.candle3_open+.01, now)
                elif case == 'late_entry':
                    w.observe(w.plan.candle3_open-w.plan.impulse_distance*.5, now)
                else:
                    w.last_ms -= 4000
                calls = []
                async def telegram(method, payload):
                    calls.append(method)
                    return {'ok':True, 'result':{'message_id':123}}
                s.telegram_call = telegram
                with patch.object(bot.time, 'time', return_value=now/1000):
                    await s.send_watch(w)
                self.assertEqual(calls, [])
                self.assertFalse(w.alerted)
                self.assertFalse(s.store.data['alerts'])

    async def test_no_level_delivery_refreshes_valid_price_after_chart_render(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            w, btc = recorded_watch()
            seed(s, w, btc)
            s.cfg.send_charts = True
            before = w.last_price
            calls = []
            async def photo(payload,png):
                calls.append(payload);return {'ok':True,'result':{'message_id':123}}
            s.telegram_photo = photo
            async def render(*args):
                w.observe(w.last_price-.03, w.last_ms+1)
                return b'chart'
            with patch.object(bot.asyncio, 'to_thread', side_effect=render):
                self.assertEqual(await self.deliver(s, w), [])
            self.assertTrue(w.alerted)
            self.assertEqual(len(calls),1)
            self.assertAlmostEqual(s.store.data['alerts'][w.key]['setup']['entry'],before-.03)


if __name__ == '__main__':
    unittest.main()
