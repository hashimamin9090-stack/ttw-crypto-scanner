"""Recorded QCOM geometry and synthetic causal events; no live orders/messages."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ttw_v3 as bot
from futures_feed import FuturesScanner
from test_ttw_v3 import candle, reciprocal
from test_ttw_v352 import scanner


def qcom_bars(bear=False):
    data = json.loads((Path(__file__).parent/'fixtures/qcom_4h_20261102.json').read_text())
    rows = [bot.Candle(*r) for r in data['bars']]
    return list(map(reciprocal, rows)) if bear else rows


def live_watch(bear=False, contact=True):
    rows = qcom_bars()
    duration = 14400000
    prior = [candle(rows[0].open_time-i*duration, 166, 166.4, 165.7, 166.2, duration)
             for i in (3, 2, 1)]
    rows[-1] = replace(rows[-1], high=rows[-1].open, low=rows[-1].open,
                       close=rows[-1].open, volume=1)
    rows = prior+rows
    if bear:
        rows = list(map(reciprocal, rows))
    side = 'BEARISH' if bear else 'BULLISH'
    tick = .001 if bear else .01
    plan = bot.make_plan(rows[-3:], side, tick, .25, .15, c2_wick_contact=contact)
    key = f'QCOMUSDT|4H|{side}|{rows[-1].open_time}'
    w = bot.Watch(key, 'QCOMUSDT', '4H', rows, plan)
    w.verified_ms = rows[-1].open_time
    # Deliberately synthetic ordered events test the live state machine.
    # They do not claim the exact historical path of the captured candle.
    for offset, price in ((100,166.12), (200,165.82), (300,166.19)):
        w.observe(10000/price if bear else price, w.verified_ms+offset)
    return w


class WickContactGeometry(unittest.TestCase):
    def test_recorded_qcom_body_clear_contact_passes_both_directions(self):
        for bear in (False, True):
            rows = qcom_bars(bear)
            side = 'BEARISH' if bear else 'BULLISH'
            tick = .001 if bear else .01
            with self.subTest(side=side):
                self.assertIsNone(bot.geometry(rows, side, tick, .25, .15))
                result = bot.geometry(rows, side, tick, .25, .15, c2_wick_contact=True)
                self.assertIsNotNone(result)
                self.assertEqual(result['c2_contact'], 'WICK_CONTACT')
                plan = bot.make_plan(rows, side, tick, .25, .15, c2_wick_contact=True)
                tip = rows[-1].high if bear else rows[-1].low
                self.assertLessEqual(plan.lower, tip)
                self.assertGreaterEqual(plan.upper, tip)
                old = bot.make_plan(rows, side, tick, .25, .15)
                self.assertTrue(tip > old.upper if not bear else tip < old.lower)

    def test_body_cut_remains_invalid_even_when_wick_crosses(self):
        rows = qcom_bars()
        rows[1] = replace(rows[1], close=165.94)
        for bear in (False, True):
            bars = list(map(reciprocal, rows)) if bear else rows
            self.assertIsNone(bot.geometry(bars, 'BEARISH' if bear else 'BULLISH',
                .001 if bear else .01, .25, .15, c2_wick_contact=True))

    def test_near_miss_allowance_is_not_widened(self):
        for bear in (False, True):
            for low, accepted in ((165.98, True), (166.00, False)):
                rows = qcom_bars()
                rows[1] = replace(rows[1], low=low)
                if bear:
                    rows = list(map(reciprocal, rows))
                for contact in (False, True):
                    self.assertEqual(bot.geometry(rows, 'BEARISH' if bear else 'BULLISH',
                        .001 if bear else .01, .25, .15, c2_wick_contact=contact) is not None, accepted)

    def test_missing_or_tiny_c3_wick_cannot_force_contact(self):
        for distance in (0, .01, .02):
            rows = qcom_bars()
            rows[-1] = replace(rows[-1], low=rows[-1].open-distance)
            self.assertIsNone(bot.geometry(rows, 'BULLISH', .01, .25, .15, c2_wick_contact=True))

    def test_price_scaling_preserves_contact_and_projected_zone(self):
        rows = qcom_bars()
        scaled = [replace(b, open=b.open*100, high=b.high*100, low=b.low*100,
                          close=b.close*100) for b in rows]
        a = bot.make_plan(rows, 'BULLISH', .01, .25, .15, c2_wick_contact=True)
        b = bot.make_plan(scaled, 'BULLISH', 1, .25, .15, c2_wick_contact=True)
        self.assertAlmostEqual(a.lower*100, b.lower)
        self.assertAlmostEqual(a.upper*100, b.upper)
        self.assertIsNotNone(bot.geometry(scaled, 'BULLISH', 1, .25, .15, c2_wick_contact=True))


class WickContactScope(unittest.TestCase):
    def test_only_enabled_spot_btc_and_selected_stock_perps_get_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            spot = scanner(folder)
            futures = scanner(folder, FuturesScanner)
            symbols = [s+'USDT' for s in bot.SPOT_WATCHLIST+bot.FUTURES_WATCHLIST]
            for symbol in symbols:
                self.assertEqual(spot.uses_wick_contact_policy(symbol), symbol == 'BTCUSDT')
                self.assertEqual(futures.uses_wick_contact_policy(symbol),
                                 symbol in {s+'USDT' for s in bot.EQUITY_WATCHLIST})
            spot.cfg.quality_enabled = futures.cfg.quality_enabled = False
            self.assertFalse(spot.uses_wick_contact_policy('BTCUSDT'))
            self.assertFalse(futures.uses_wick_contact_policy('QCOMUSDT'))

    def test_status_explains_current_policy_and_previous_release_remains_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            self.assertIn('BTC and stocks: C2 wick contact', s.status_text())
            self.assertIn('3.6.0', bot.COMPATIBLE_VERSIONS)


class WickContactSequence(unittest.TestCase):
    def test_contact_then_live_colour_flip_can_trigger_without_close(self):
        for bear in (False, True):
            w = live_watch(bear)
            self.assertTrue(w.ready(w.last_ms, .4), (w.failed, w.plan))
            self.assertLess(w.last_ms, w.bars[-1].close_time)
            old = live_watch(bear, contact=False)
            self.assertFalse(old.ready(old.last_ms, .4))

    def test_wrong_colour_doji_and_stale_quote_remain_blocked(self):
        for bear in (False, True):
            w = live_watch(bear)
            self.assertFalse(w.ready(w.last_ms+3001, .4))
            w.last_price = w.plan.candle3_open
            self.assertFalse(w.ready(w.last_ms, .4))
            w.last_price += .01 if bear else -.01
            self.assertFalse(w.ready(w.last_ms, .4))

    def test_impulse_before_touch_and_late_return_remain_permanent_rejections(self):
        for bear in (False, True):
            for after_touch in (False, True):
                w = live_watch(bear)
                if not after_touch:
                    rows = w.bars.copy()
                    c3 = rows[-1]
                    rows[-1] = replace(c3, high=c3.open, low=c3.open, close=c3.open)
                    w = bot.Watch(w.key, w.symbol, w.timeframe, rows, w.plan)
                    w.verified_ms = c3.open_time
                sign = -1 if bear else 1
                now = w.bars[-1].open_time+1000
                w.observe(w.plan.candle3_open+sign*w.plan.impulse_distance*1.1, now)
                self.assertEqual(w.failed, 'IMPULSE_ALREADY_AFTER_TOUCH' if after_touch else 'IMPULSE_BEFORE_TOUCH')
                w.observe(w.plan.candle3_open+sign*.01, now+100)
                self.assertFalse(w.ready(now+100, .4))

    def test_near_miss_side_of_zone_is_still_a_hard_boundary(self):
        for bear in (False, True):
            w = live_watch(bear)
            w.observe(w.plan.upper+.01 if bear else w.plan.lower-.01, w.last_ms+100)
            self.assertEqual(w.failed, 'GEOMETRY_ZONE_EXCEEDED')


class WickContactLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_production_discovery_selects_policy_and_requires_verification(self):
        for cls, symbol, contact in ((bot.Scanner,'BTCUSDT',True),
                                     (FuturesScanner,'QCOMUSDT',True),
                                     (bot.Scanner,'ETHUSDT',False),
                                     (FuturesScanner,'XAUUSDT',False)):
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder, cls)
                s.tick_sizes[symbol] = .01
                w = live_watch()
                rows = w.bars
                async def bases(symbol): return rows
                async def verify(watch): raise RuntimeError('test requires causal history')
                s.fetch_symbol_bases = bases
                s.candles_for_timeframe = lambda data, tf: data if tf == '4H' else []
                s.verify_watch = verify
                with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                    await s.scan_symbol(symbol)
                key = f'{symbol}|4H|BULLISH|{rows[-1].open_time}'
                self.assertIn(key, s.watches, dict(s.stats))
                self.assertEqual(s.watches[key].plan.c2_wick_contact, contact)
                self.assertFalse(s.watches[key].ready(w.last_ms, .4))
                self.assertFalse(s.store.data['alerts'])

    async def test_new_policy_survives_restart_while_old_alert_keeps_its_geometry(self):
        for contact in (False, True):
            with self.subTest(contact=contact), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder, FuturesScanner)
                w = live_watch()
                setup = s.stamp_setup(bot.make_setup(w, .1, True))
                if not contact:
                    setup.pop('geometry_c2_wick_contact')
                    setup['strategy_version'] = '3.6.0'
                s.store.data['alerts'][w.key] = dict(status='sent', setup=deepcopy(setup))
                s.store.save()
                s = scanner(folder, FuturesScanner)
                s.tick_sizes['QCOMUSDT'] = .01
                async def bases(symbol): return w.bars
                s.fetch_symbol_bases = bases
                s.candles_for_timeframe = lambda data, tf: data if tf == '4H' else []
                with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                    await s.scan_symbol('QCOMUSDT')
                self.assertEqual(s.watches[w.key].plan.c2_wick_contact, contact)
                self.assertEqual(s.store.data['alerts'][w.key]['setup'], setup)

    async def test_candle_confirmation_uses_recorded_policy_for_alerts_and_shadows(self):
        for contact in (False, True):
            with self.subTest(contact=contact), tempfile.TemporaryDirectory() as folder:
                s = scanner(folder, FuturesScanner)
                w = live_watch()
                setup = s.stamp_setup(bot.make_setup(w, .1, True))
                if not contact:
                    setup.pop('geometry_c2_wick_contact')
                    setup['strategy_version'] = '3.6.0'
                s.store.data['alerts'][w.key] = dict(status='sent', setup=deepcopy(setup))
                s.store.data['shadow_entries'][w.key] = dict(setup=deepcopy(setup), baseline='TEST',
                    first_quality={'reason':'TEST'}, hypothetical=True)
                async def edit(*args): pass
                s.edit_record = edit
                await s.close_updates(w.symbol, w.timeframe, w.bars, w.bars[-1].close_time+1)
                expected = 'CONFIRMED' if contact else 'INVALID'
                self.assertEqual(s.store.data['alerts'][w.key]['close_verdict'], expected)
                self.assertEqual(s.store.data['shadow_entries'][w.key]['close_verdict'], expected)

    async def test_delivery_rechecks_contact_and_keeps_exactly_one_alert(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder, FuturesScanner)
            w = live_watch()
            calls = []
            async def telegram(method, payload):
                calls.append(method)
                return {'ok':True, 'result':{'message_id':123}}
            s.telegram_call = telegram
            # Isolate delivery from unrelated context gates; those retain the
            # existing full-suite tests. Live geometry is never mocked here.
            quality = dict(ok=True, reason='QUALITY_READY')
            with patch.object(s, 'quality_check', return_value=quality), \
                    patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                await s.send_watch(w)
                await s.send_watch(w)
            self.assertEqual(calls, ['sendMessage'])
            self.assertTrue(s.store.data['alerts'][w.key]['setup']['geometry_c2_wick_contact'])


if __name__ == '__main__':
    unittest.main()
