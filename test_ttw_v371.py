"""Recorded SNDK location regression and isolated delivery; no live messages."""
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
from test_ttw_v37 import enable


def sndk_watch():
    fixture = json.loads((Path(__file__).parent / 'fixtures/sndk_4h_20261105.json').read_text())
    bars = [bot.Candle(*row) for row in fixture['bars']]
    plan = bot.make_plan(bars[-3:], 'BULLISH', .01, .25, .15, c2_wick_contact=True)
    key = f"SNDKUSDT|4H|BULLISH|{bars[-1].open_time}"
    watch = bot.Watch(key, 'SNDKUSDT', '4H', bars, plan)
    # Controlled already-verified state. The exchange snapshot alone cannot
    # establish touch-first trade ordering, which remains a production gate.
    watch.verified_ms = watch.touch_ms = fixture['recorded_touch_ms']
    watch.last_ms = fixture['observed_at_ms']
    watch.last_price = bars[-1].close
    watch.extreme = bars[-1].low
    return watch


class EquityLocationPolicy(unittest.TestCase):
    def test_only_selected_enabled_equity_intraday_profiles_are_relaxed(self):
        with tempfile.TemporaryDirectory() as folder:
            stock = enable(scanner(folder, FuturesScanner))
            spot = enable(scanner(folder))
            for symbol in [s+'USDT' for s in bot.EQUITY_WATCHLIST]:
                for tf in bot.TIMEFRAMES:
                    profile = stock.entry_profile(symbol, tf)
                    self.assertEqual(stock.uses_equity_intraday_policy(symbol, tf), tf.endswith('H'))
                    self.assertFalse(profile['require_location'])
                    self.assertEqual(profile.get('location_policy'),
                                     'EQUITY_INTRADAY_CONTEXT_ONLY' if tf.endswith('H') else None)
                    # Geometry, approach, reclaim, entry movement and stop
                    # policy are unchanged by the location exemption.
                    expected = context.timeframe_profile(tf)
                    expected.update(required_stop_policy='SL1', btc_expansion_veto=False)
                    if tf.endswith('H'):
                        expected.update(require_location=False,
                                        location_policy='EQUITY_INTRADAY_CONTEXT_ONLY')
                    self.assertEqual(profile, expected)
                self.assertTrue(spot.entry_profile(symbol, '4H')['require_location'])
            for symbol in ('XAUUSDT', 'XAGUSDT', 'BTCUSDT', 'ETHUSDT', 'UNKNOWNUSDT'):
                self.assertTrue(stock.entry_profile(symbol, '4H')['require_location'])
            self.assertFalse(spot.entry_profile('BTCUSDT', '4H')['require_location'])
            self.assertEqual(spot.entry_profile('BTCUSDT', '4H')['location_policy'],
                             'BTC_INTRADAY_CONTEXT_ONLY')
            stock.cfg.quality_enabled = False
            self.assertTrue(stock.entry_profile('SNDKUSDT', '4H')['require_location'])
            with self.assertRaises(ValueError):
                stock.entry_profile('SNDKUSDT', 'badH')

    def test_recorded_sndk_passes_missing_level_but_preserves_other_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            stock = enable(scanner(folder, FuturesScanner))
            w = sndk_watch()
            setup = stock.stamp_setup(bot.make_setup(w, .1, True))
            self.assertTrue(w.ready(w.last_ms, .4))
            q = stock.quality_check(w, setup, w.last_ms)
            self.assertTrue(q['ok'], q)
            self.assertEqual(q['location']['reason'], 'NO_ESTABLISHED_LEVEL')
            self.assertAlmostEqual(q['min_reclaim'], 1.644)
            self.assertEqual(q['location']['obstacle']['level'], 1148.97)
            self.assertTrue(q['approach']['ok'])
            previous = dict(q['profile'], require_location=True)
            previous.pop('location_policy')
            before = context.entry_quality(w.bars, [], setup, q['btc'],
                precision=True, profile=previous, structure_targets=True)
            self.assertEqual(before['reason'], 'NO_ESTABLISHED_LEVEL')
            self.assertFalse(before['ok'])

            weak = dict(setup, entry=w.bars[-1].open+.55)
            self.assertEqual(stock.quality_check(w, weak, w.last_ms)['reason'], 'RECLAIM_TOO_SMALL')
            too_small = dict(setup, tp1=setup['entry']+1, targets_frozen=True)
            self.assertEqual(stock.quality_check(w, too_small, w.last_ms)['reason'],
                             'TARGET_TOO_SMALL_FOR_RISK')
            beyond = dict(setup, tp1=1200, targets_frozen=True)
            self.assertEqual(stock.quality_check(w, beyond, w.last_ms)['reason'],
                             'TARGET_BEYOND_OPPOSING_LEVEL')
            wrong_stop = dict(setup, sl2=setup['entry']+1)
            self.assertEqual(stock.quality_check(w, wrong_stop, w.last_ms)['reason'],
                             'C2_STOP_NOT_PROTECTIVE')
            # A body crossing the wick line cannot become eligible merely
            # because the historical level prerequisite is optional.
            w.bars[-2] = replace(w.bars[-2], close=w.bars[-2].low)
            self.assertFalse(w.ready(w.last_ms, .4))

    def test_unverified_or_untouched_snapshot_cannot_trigger(self):
        w = sndk_watch()
        w.verified_ms = 0
        self.assertFalse(w.ready(w.last_ms, .4))
        w = sndk_watch()
        w.touch_ms = 0
        self.assertFalse(w.ready(w.last_ms, .4))


class RecordedStockDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_real_quality_checks_allow_one_mocked_delivery_without_level(self):
        with tempfile.TemporaryDirectory() as folder:
            stock = enable(scanner(folder, FuturesScanner))
            w = sndk_watch()
            w.stream_epoch = stock.stream_epoch
            calls = []
            async def telegram(method, payload):
                calls.append((method, payload))
                return {'ok': True, 'result': {'message_id': 123}}
            stock.telegram_call = telegram
            with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                await stock.send_watch(w)
                await stock.send_watch(w)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'sendMessage')
            r = stock.store.data['alerts'][w.key]
            self.assertEqual(r['status'], 'sent')
            self.assertEqual(r['setup']['market'], 'BINANCE_FUTURES')
            self.assertEqual(r['setup']['entry_stage'], 'EARLY_C3')
            self.assertEqual(r['entry_quality']['reason'], 'QUALITY_READY')
            self.assertFalse(r['entry_quality']['location']['ok'])
            self.assertEqual(r['setup']['geometry_c2_breach_fraction'], .5)
            self.assertAlmostEqual(r['setup']['gap_pct'], .05389571733909193)
            self.assertAlmostEqual(r['setup']['sl1'], 996.14286)
            self.assertAlmostEqual(r['setup']['sl2'], 993.33567)
            self.assertIn('3.7.0', bot.COMPATIBLE_VERSIONS)


if __name__ == '__main__':
    unittest.main()
