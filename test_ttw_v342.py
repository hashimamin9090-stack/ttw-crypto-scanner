"""Replay captured BTC target/room evidence, not a historical price backtest."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import os
import tempfile
import unittest
from unittest.mock import patch

import ttw_context as context
import ttw_outcomes as outcomes
import ttw_v3 as bot
from test_ttw_v3 import candle
import test_ttw_v33 as legacy


OPEN = 1791878400000
NOW = 1791880711173
MIRROR_BASE = 2*83848.


def btc_setup():
    # Prices/stop bases and first qualifying colour state from Render logs.
    return dict(strategy_version=bot.VERSION, symbol='BTCUSDT', timeframe='2H',
                direction='BEARISH', entry=83848., current_price=83848.,
                sl1=84252.168, sl2=84059.69*1.001,
                tp1=83848*.97, tp2=83848*.95, tp_pct1=3, tp_pct2=5,
                tick_size=.01, wick1=83913.82, wick2=84059.69, wick3=84168.,
                candle3_open_time=OPEN, candle3_close_time=OPEN+7200000-1)


def logged_location():
    return dict(ok=True, reason='ESTABLISHED_LEVEL', setup_atr=763.6185714285706,
                level=dict(level=84199., kind='SWING_HIGH', source='SETUP',
                           known_at_ms=1791748799999, distance=31.),
                obstacle=dict(level=83186., kind='SWING_LOW_RECLAIMED',
                              source='PARENT', known_at_ms=1791806399999, distance=662.))


def rows():
    # Synthetic completed OHLC supplies approach/reclaim/geometry context.
    step = 7200000
    start = OPEN-2*step
    prior = [candle(start-(5-i)*step, 82400+i*150, 82650+i*150,
                    82350+i*150, 82550+i*150, step) for i in range(5)]
    return prior+[candle(start, 83315., 83913.82, 83205., 83638.82, step),
                  candle(start+step, 83638.82, 84059.69, 83597.43, 83870.89, step),
                  candle(OPEN, 83870.89, 84168., 83790., 83848., step)]


def mirror(s, bars, location):
    s = dict(s, direction='BULLISH')
    for key in ('entry','current_price','sl1','sl2','tp1','tp2','wick1','wick2','wick3'):
        s[key] = MIRROR_BASE-s[key]
    bars = [replace(b, open=MIRROR_BASE-b.open, high=MIRROR_BASE-b.low,
                    low=MIRROR_BASE-b.high, close=MIRROR_BASE-b.close) for b in bars]
    location = deepcopy(location)
    for key in ('level', 'obstacle'):
        location[key]['level'] = MIRROR_BASE-location[key]['level']
    return s, bars, location


class StructureTargets(unittest.TestCase):
    def quality(self, s=None, bars=None, location=None, enabled=True, btc=None):
        s, bars, location = s or btc_setup(), bars or rows(), location or logged_location()
        with patch.object(context, 'location_context', return_value=location):
            return context.entry_quality(bars, [], s,
                btc or dict(ok=True, regime='ORDERLY_OR_RANGE'), precision=True,
                profile=context.timeframe_profile(s['timeframe']), structure_targets=enabled)

    def test_captured_btc_target_veto_now_passes_both_existing_risk_floors(self):
        s = btc_setup(); original = deepcopy(s)
        self.assertEqual(self.quality(s, enabled=False)['reason'], 'TARGET_BEYOND_OPPOSING_LEVEL')
        result = self.quality(s)
        self.assertTrue(result['ok'], result)
        plan = result['target_plan']
        self.assertAlmostEqual(plan['tp1'], 83224.19)
        self.assertAlmostEqual(plan['tp_pct1'], .7439771968, places=8)
        self.assertGreater(result['risk_checks']['sl1']['target_r'], 1.5)
        self.assertGreater(result['risk_checks']['sl2']['target_r'], 1.5)
        self.assertEqual(plan['extension_tp1'], original['tp1'])
        self.assertEqual(s, original)

    def test_bullish_and_bearish_targets_round_before_the_level_buffer(self):
        for bull in (False, True):
            s, bars, location = btc_setup(), rows(), logged_location()
            if bull:
                s, bars, location = mirror(s, bars, location)
            result = self.quality(s, bars, location)
            self.assertTrue(result['ok'], result)
            plan = result['target_plan']; sign = 1 if bull else -1
            self.assertGreaterEqual(sign*(location['obstacle']['level']-plan['tp1']), plan['target_buffer'])
            self.assertLess(abs(plan['tp1']-s['entry']), abs(s['tp1']-s['entry']))
            self.assertEqual(round(plan['tp1']/s['tick_size'], 6) % 1, 0)

    def test_later_entry_with_insufficient_reward_is_still_blocked(self):
        s = btc_setup(); s.update(entry=83776., current_price=83776.)
        location = logged_location(); location['obstacle']['distance'] = 590.
        result = self.quality(s, location=location)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'TARGET_TOO_SMALL_FOR_RISK')

    def test_wide_c2_stop_cannot_pass_using_the_narrower_c3_stop(self):
        s = btc_setup(); s['sl2'] = 85000.
        result = self.quality(s)
        self.assertEqual(result['reason'], 'C2_TARGET_TOO_SMALL_FOR_RISK')

    def test_colour_reclaim_btc_and_required_level_checks_still_block(self):
        s = btc_setup(); s['entry'] = 83870.
        self.assertEqual(self.quality(s)['reason'], 'RECLAIM_TOO_SMALL')
        self.assertEqual(self.quality(btc=dict(ok=False, regime='UNKNOWN'))['reason'], 'BTC_DATA_UNAVAILABLE')
        self.assertEqual(self.quality(btc=dict(ok=True, regime='UP_EXPANSION'))['reason'], 'BTC_OPPOSING_EXPANSION')
        location = logged_location(); location.update(ok=False, reason='NO_ESTABLISHED_LEVEL', level=None)
        self.assertEqual(self.quality(location=location)['reason'], 'NO_ESTABLISHED_LEVEL')

    def test_clear_path_and_already_safe_first_target_remain_unchanged(self):
        location = logged_location(); location['obstacle'] = None
        result = self.quality(location=location)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('target_plan', result)
        s = btc_setup(); s['tp1'] = 83230.
        result = self.quality(s)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('target_plan', result)

    def test_higher_timeframe_keeps_previous_target_gate(self):
        s = btc_setup(); s['timeframe'] = '1W'
        self.assertEqual(self.quality(s)['reason'], 'TARGET_BEYOND_OPPOSING_LEVEL')

    def test_frozen_target_cannot_move_to_fit_a_new_obstacle(self):
        s = btc_setup(); s.update(self.quality(s)['target_plan'])
        original = deepcopy(s)
        further = logged_location(); further['obstacle'].update(level=83000., distance=848.)
        result = self.quality(s, location=further)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('target_plan', result)
        closer = logged_location(); closer['obstacle'].update(level=83220., distance=628.)
        self.assertEqual(self.quality(s, location=closer)['reason'], 'TARGET_BEYOND_OPPOSING_LEVEL')
        self.assertEqual(s, original)

    def test_coarse_ticks_round_towards_entry_without_widening_room(self):
        for bull in (False, True):
            s, bars, location = btc_setup(), rows(), logged_location()
            if bull:
                s, bars, location = mirror(s, bars, location)
            s['tick_size'] = .5
            plan = context.structure_target(s, location)
            self.assertEqual(Decimal(str(plan['tp1'])) % Decimal('.5'), 0)
            sign = 1 if bull else -1
            self.assertGreaterEqual(sign*(location['obstacle']['level']-plan['tp1']), plan['target_buffer'])

    def test_production_default_enables_structure_targets_and_allows_focused_rollback(self):
        for value, expected in (('true', True), ('false', False)):
            with patch.dict(os.environ, dict(TELEGRAM_CHAT_ID='test', TELEGRAM_BOT_TOKEN='test',
                                            V342_STRUCTURE_TARGETS=value), clear=True):
                self.assertEqual(bot.Config().structure_targets, expected)


class StructureDelivery(unittest.IsolatedAsyncioTestCase):
    def scanner_watch(self, folder, bull=False):
        scanner = legacy.QualityIntegration().scanner(folder)
        scanner.cfg.precision_enabled = scanner.cfg.structure_targets = True
        scanner.stream_connected = True
        bars = rows(); p = context.timeframe_profile('2H')
        if bull:
            _, bars, _ = mirror(btc_setup(), bars, logged_location())
        side = 'BULLISH' if bull else 'BEARISH'
        plan = bot.make_plan(bars[-3:], side, .01,
                             p['geometry_wick_fraction'], p['geometry_price_cap_pct'])
        self.assertIsNotNone(plan)
        key = f'BTCUSDT|2H|{side}|{OPEN}'
        watch = bot.Watch(key, 'BTCUSDT', '2H', bars, plan)
        watch.last_ms = watch.verified_ms = NOW
        watch.touch_ms = OPEN+799110
        watch.extreme = MIRROR_BASE-84168. if bull else 84168.
        watch.last_price = MIRROR_BASE-83848. if bull else 83848.
        scanner.watches[key] = watch
        scanner.symbols = ['BTCUSDT']; scanner.symbol_last_id['BTCUSDT'] = 10
        return scanner, watch

    async def test_delivered_caption_setup_and_outcomes_share_the_shorter_tp1(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner, watch = self.scanner_watch(folder)
            captions = []
            async def telegram(method, payload):
                captions.append(payload['text'])
                return {'ok':True, 'result':{'message_id':123}}
            scanner.telegram_call = telegram
            self.assertTrue(watch.ready(NOW, .4))
            with patch.object(context, 'location_context', return_value=logged_location()), \
                 patch.object(bot, 'btc_context', return_value=dict(ok=True, regime='ORDERLY_OR_RANGE')), \
                 patch.object(bot.time, 'time', return_value=NOW/1000):
                await scanner.send_watch(watch)
            self.assertTrue(watch.alerted)
            saved = scanner.store.data['alerts'][watch.key]
            s = saved['setup']
            self.assertAlmostEqual(s['tp1'], 83224.19)
            self.assertIn('TP1: 83,224.19 (0.74%)', captions[0])
            self.assertIn('Extensions:', captions[0])
            self.assertIn('(3–5%) · after level breaks', captions[0])
            scanner.accept_trade('BTCUSDT', 11, NOW+100, s['tp1'])
            for stop in ('sl1', 'sl2'):
                self.assertEqual(saved['trade_tracking']['paths'][stop]['tp1'], 'TP')
                self.assertEqual(saved['trade_tracking']['paths'][stop]['tp2'], 'OPEN')
            self.assertAlmostEqual(outcomes.summary(saved)['paths']['sl1']['target1_r'], 1.5434423309)
            again = bot.Scanner(scanner.cfg)
            self.assertEqual(again.store.data['alerts'][watch.key]['setup'], s)

    async def test_bullish_delivery_uses_target_before_resistance(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner, watch = self.scanner_watch(folder, bull=True)
            _, _, location = mirror(btc_setup(), rows(), logged_location())
            async def telegram(method, payload):
                return {'ok':True, 'result':{'message_id':123}}
            scanner.telegram_call = telegram
            with patch.object(context, 'location_context', return_value=location), \
                 patch.object(bot, 'btc_context', return_value=dict(ok=True, regime='ORDERLY_OR_RANGE')), \
                 patch.object(bot.time, 'time', return_value=NOW/1000):
                await scanner.send_watch(watch)
            self.assertTrue(watch.alerted)
            s = scanner.store.data['alerts'][watch.key]['setup']
            self.assertAlmostEqual(s['tp1'], 84471.81)
            self.assertLess(s['tp1'], location['obstacle']['level'])

    async def test_render_recheck_blocks_changed_room_without_moving_the_target(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner, watch = self.scanner_watch(folder)
            scanner.cfg.send_charts = True
            seen = []; current_location = logged_location()
            async def render(function, bars, setup):
                seen.append(deepcopy(setup))
                current_location['obstacle'].update(level=83220., distance=628.)
                return b'png'
            async def no_send(*args, **kwargs):
                self.fail('A changed opposing level must not be delivered')
            scanner.telegram_photo = no_send
            with patch.object(context, 'location_context', side_effect=lambda *args, **kwargs:deepcopy(current_location)), \
                 patch.object(bot, 'btc_context', return_value=dict(ok=True, regime='ORDERLY_OR_RANGE')), \
                 patch.object(bot.time, 'time', return_value=NOW/1000), \
                 patch.object(bot.asyncio, 'to_thread', side_effect=render):
                await scanner.send_watch(watch)
            self.assertAlmostEqual(seen[0]['tp1'], 83224.19)
            self.assertFalse(watch.alerted)
            self.assertEqual(scanner.store.data['alerts'], {})


if __name__ == '__main__':
    unittest.main()
