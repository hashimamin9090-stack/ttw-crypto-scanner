import math
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch
import unittest

import ttw_context as context
import ttw_v3 as bot
from test_ttw_v3 import candle, fixture, reciprocal
import test_ttw_v33 as legacy


def near_candles():
    # Exact Binance spot 3H OHLC, 11 October 2026 at 03:00/06:00/09:00 UTC.
    duration = 10800000
    return [candle(1791687600000, 5.297, 5.313, 5.169, 5.228, duration),
            candle(1791698400000, 5.228, 5.308, 5.196, 5.268, duration),
            candle(1791709200000, 5.268, 5.304, 5.209, 5.290, duration)]


def limits(tf):
    p = context.timeframe_profile(tf)
    return p['geometry_wick_fraction'], p['geometry_price_cap_pct']


class BoundedGeometry(unittest.TestCase):
    def test_exact_near_requires_both_limits_and_passes_new_intraday_geometry(self):
        for bear in (False, True):
            rows = list(map(reciprocal, near_candles())) if bear else near_candles()
            side = 'BEARISH' if bear else 'BULLISH'
            self.assertIsNone(bot.geometry(rows, side, .001, .20, .10))
            self.assertIsNone(bot.geometry(rows, side, .001, .20, .15))
            result = bot.geometry(rows, side, .001, *limits('3H'))
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result['wick_fraction'], .2207806558, places=7)
            plan = bot.make_plan(rows, side, .001, *limits('3H'))
            self.assertIsNotNone(plan)
            tip = rows[-1].high if bear else rows[-1].low
            self.assertLessEqual(plan.lower, tip)
            self.assertGreaterEqual(plan.upper, tip)

    def test_price_cap_still_limits_long_wicks_and_higher_is_only_modestly_wider(self):
        for gap, intraday, higher in ((.0018, False, True), (.003, False, False)):
            rows = [candle(0, 102, 104, 100, 101.5),
                    candle(3600000, 102, 104, 100*math.exp(gap), 101.5),
                    candle(7200000, 101.5, 104, 100, 102)]
            for bear in (False, True):
                bars = list(map(reciprocal, rows)) if bear else rows
                side = 'BEARISH' if bear else 'BULLISH'
                self.assertEqual(bool(bot.geometry(bars, side, .001, *limits('3H'))), intraday)
                self.assertEqual(bool(bot.geometry(bars, side, .001, *limits('1W'))), higher)

    def test_small_price_gap_cannot_bypass_small_anchor_wick_limit(self):
        rows = [candle(0, 100.2, 100.3, 100, 100.1),
                candle(3600000, 100.2, 100.3, 100.04, 100.14),
                candle(7200000, 100.2, 100.3, 100, 100.25)]
        for tf in ('3H', '1D'):
            for bear in (False, True):
                bars = list(map(reciprocal, rows)) if bear else rows
                self.assertIsNone(bot.geometry(bars, 'BEARISH' if bear else 'BULLISH',
                                               .001, *limits(tf)))

    def test_near_miss_and_slight_breach_remain_symmetric(self):
        for gap in (-.0012, .0012):
            rows = [candle(0, 102, 104, 100, 101.5),
                    candle(3600000, 102, 104, 100*math.exp(gap), 101.5),
                    candle(7200000, 101.5, 104, 100, 102)]
            for bear in (False, True):
                bars = list(map(reciprocal, rows)) if bear else rows
                self.assertIsNotNone(bot.geometry(bars, 'BEARISH' if bear else 'BULLISH',
                                                  .001, *limits('3H')))

    def test_body_cuts_missing_wicks_and_incidental_c3_wicks_remain_rejected(self):
        body_cut = fixture()
        body_cut[1] = replace(body_cut[1], open=99, close=99)
        missing_wick = fixture()
        missing_wick[-1] = replace(missing_wick[-1], low=missing_wick[-1].close)
        short_wick = fixture()
        short_wick[-1] = replace(short_wick[-1], open=98.02, low=98.01, close=98.03)
        for tf in ('3H', '1W'):
            for rows in (body_cut, missing_wick, short_wick):
                for bear in (False, True):
                    bars = list(map(reciprocal, rows)) if bear else rows
                    self.assertIsNone(bot.geometry(bars, 'BEARISH' if bear else 'BULLISH',
                                                   .001, *limits(tf)))

    def test_larger_live_c3_cannot_inflate_fixed_anchor_allowance(self):
        rows = near_candles()
        rows[-1] = replace(rows[-1], low=4.9, high=6, close=5.29)
        for tf in ('3H', '1D'):
            self.assertIsNone(bot.geometry(rows, 'BULLISH', .001, *limits(tf)))

    def test_geometry_is_invariant_to_nominal_coin_price(self):
        rows = near_candles()
        scaled = [replace(b, open=b.open*100, high=b.high*100,
                          low=b.low*100, close=b.close*100) for b in rows]
        a = bot.geometry(rows, 'BULLISH', .001, *limits('3H'))
        b = bot.geometry(scaled, 'BULLISH', .1, *limits('3H'))
        self.assertAlmostEqual(a['gap_pct'], b['gap_pct'])
        self.assertAlmostEqual(a['wick_fraction'], b['wick_fraction'])

    def test_previous_btc_and_eth_audit_failures_stay_rejected(self):
        btc = [candle(0,84600.01,84725.04,84522.63,84652.09,10800000),
               candle(10800000,84652.09,84674.01,84550,84616.01,10800000),
               candle(21600000,84616.02,84624.04,84616.01,84624.04,10800000)]
        eth = [candle(0,2677.15,2682.81,2672.34,2677.74,10800000),
               candle(10800000,2677.74,2684.05,2675.8,2683.01,10800000),
               candle(21600000,2683.01,2686.19,2683.01,2685.48,10800000)]
        for rows, tick in ((btc, .01), (eth, .01)):
            for tf in ('3H', '1W'):
                self.assertIsNone(bot.geometry(rows, 'BEARISH', tick, *limits(tf)))

    def test_new_geometry_still_requires_colour_flip_and_rejects_a_later_impulse(self):
        for bear in (False, True):
            rows = list(map(reciprocal, near_candles())) if bear else near_candles()
            side = 'BEARISH' if bear else 'BULLISH'
            plan = bot.make_plan(rows, side, .001, *limits('3H'))
            rows[-1] = replace(rows[-1], high=rows[-1].open,
                               low=rows[-1].open, close=rows[-1].open)
            watch = bot.Watch('near-colour', 'NEARUSDT', '3H', rows, plan)
            start = rows[-1].open_time
            watch.verified_ms = start
            price = lambda value: 10000/value if bear else value
            watch.observe(price(5.293), start+1)
            watch.observe(price(5.209), start+2)
            watch.observe(price(5.267), start+3)
            self.assertFalse(watch.ready(start+3, .40))
            watch.observe(price(5.273), start+4)
            self.assertTrue(watch.ready(start+4, .40))
            watch.observe(plan.candle3_open+(-1 if bear else 1)*plan.impulse_distance*1.01,
                          start+5)
            self.assertEqual(watch.failed, 'IMPULSE_ALREADY_AFTER_TOUCH')


class ProductionGeometry(unittest.IsolatedAsyncioTestCase):
    def scanner(self, folder):
        scanner = legacy.QualityIntegration().scanner(folder)
        scanner.cfg.precision_enabled = True
        scanner.cfg.geometry_wick_fraction = .20
        scanner.cfg.geometry_price_cap_pct = .10
        scanner.cfg.impulse_range_fraction = .5
        scanner.cfg.rejection_wick_fraction = .10
        scanner.cfg.max_open_gap_range = 1.5
        scanner.stream_connected = True
        scanner.tick_sizes['NEARUSDT'] = .001
        return scanner

    def rows(self):
        anchors = near_candles()
        start = anchors[0].open_time
        step = 10800000
        # Synthetic completed opposing approach; only the three anchors are exact.
        prior = [candle(start-3*step, 5.38, 5.44, 5.33, 5.36, step),
                 candle(start-2*step, 5.36, 5.43, 5.30, 5.34, step),
                 candle(start-step, 5.34, 5.37, 5.27, 5.297, step)]
        return prior+anchors

    async def scan(self, scanner, rows):
        now = rows[-1].open_time+1000
        async def bases(symbol):
            return rows
        async def defer(watch):
            raise RuntimeError('History unavailable in fixture')
        scanner.fetch_symbol_bases = bases
        scanner.candles_for_timeframe = lambda bases, tf: bases if tf == '3H' else []
        scanner.verify_watch = defer
        with patch.object(bot.time, 'time', return_value=now/1000):
            await scanner.scan_symbol('NEARUSDT')

    async def test_actual_config_old_environment_cannot_override_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'TELEGRAM_CHAT_ID':'test', 'TELEGRAM_BOT_TOKEN':'test',
                    'STATE_DIR':folder, 'V3_GEOMETRY_WICK_FRACTION':'.20',
                    'V3_GEOMETRY_PRICE_CAP_PCT':'.10'}, clear=True):
                scanner = bot.Scanner(bot.Config())
            for tf in bot.TIMEFRAMES:
                self.assertEqual(scanner.geometry_limits(tf), (.25,.15) if tf.endswith('H') else (.30,.25))
            scanner.cfg.quality_enabled = False
            self.assertEqual(scanner.geometry_limits('3H'), (.20,.10))

    async def test_production_scan_arms_near_without_bypassing_history_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            await self.scan(scanner, self.rows())
            key = 'NEARUSDT|3H|BULLISH|1791709200000'
            self.assertIn(key, scanner.watches, dict(scanner.stats))
            watch = scanner.watches[key]
            self.assertEqual((watch.plan.geometry_fraction, watch.plan.price_cap_pct), (.25,.15))
            self.assertFalse(watch.failed)
            self.assertEqual(watch.verified_ms, 0)
            self.assertFalse(watch.ready(watch.bars[-1].open_time+1000, .4))
            self.assertNotIn(key, scanner.store.data['sequence_rejections'])
            self.assertEqual(scanner.store.data['alerts'], {})

    async def test_restored_v340_alert_retains_frozen_old_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            rows = self.rows()
            rows[-1] = replace(rows[-1], low=5.222, high=5.273, close=5.273)
            plan = bot.make_plan(rows[-3:], 'BULLISH', .001, .20, .10)
            key = 'NEARUSDT|3H|BULLISH|1791709200000'
            watch = bot.Watch(key, 'NEARUSDT', '3H', rows, plan)
            watch.last_price = 5.273
            setup = dict(bot.make_setup(watch, .1), strategy_version='3.4.0')
            scanner.store.data['alerts'][key] = dict(status='sent', setup=setup)
            await self.scan(scanner, rows)
            restored = scanner.watches[key]
            self.assertTrue(restored.alerted)
            self.assertEqual((restored.plan.geometry_fraction, restored.plan.price_cap_pct), (.20,.10))
            self.assertEqual(scanner.store.data['alerts'][key]['setup'], setup)

    async def test_geometry_rejection_records_actual_gap_and_selected_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            rows = near_candles()
            plan = bot.make_plan(rows, 'BULLISH', .001, .20, .10)
            watch = bot.Watch('near-old-bound', 'NEARUSDT', '3H', rows, plan)
            scanner.reject(watch, 'GEOMETRY_ZONE_EXCEEDED')
            evidence = scanner.store.data['sequence_rejections'][watch.key]
            self.assertEqual(evidence['wick_tips'], [5.169,5.196,5.209])
            self.assertAlmostEqual(evidence['c2_gap_pct'], .1356445461)
            self.assertEqual(evidence['geometry_price_cap_pct'], .10)
            self.assertEqual(evidence['geometry_fraction'], .20)

    async def test_new_private_observations_identify_changed_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            watch = legacy.QualityIntegration().qualifying_watch()
            scanner.record_shadow(watch, {'ok':False,'reason':'NO_ESTABLISHED_LEVEL'})
            record = scanner.store.data['shadow_entries'][watch.key]
            self.assertEqual(record['baseline'], 'LEGACY_ENTRY_WINDOW_3.4.1_GEOMETRY')


if __name__ == '__main__':
    unittest.main()
