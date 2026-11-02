"""Recorded bad-alert geometry and bounded QCOM allowance; no live messages."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ttw_v3 as bot
from futures_feed import FuturesScanner
from test_ttw_v3 import candle, reciprocal
from test_ttw_v352 import scanner
from test_ttw_v361 import live_watch, qcom_bars


def bad_examples():
    return json.loads((Path(__file__).parent /
                       'fixtures/excessive_c2_contacts_20261102.json').read_text())['examples']


def recorded_watch(example, *, legacy=False):
    bars = [bot.Candle(*r) for r in example['bars']]
    plan = bot.make_plan(bars, example['direction'], example['tick_size'],
                         example['geometry_fraction'], example['geometry_price_cap_pct'],
                         c2_wick_contact=True,
                         c2_breach_fraction=None if legacy else bot.C2_CONTACT_BREACH_FRACTION)
    if plan is None:
        return None
    w = bot.Watch(example['key'], example['key'].split('|')[0],
                  example['key'].split('|')[1], bars, plan)
    # Controlled already-verified state isolates geometry and delivery gates.
    # These fields do not reconstruct historical trade ordering.
    w.verified_ms = w.touch_ms = bars[-1].open_time+1
    w.last_ms = bars[-1].open_time+1000
    w.last_price = bars[-1].close
    w.extreme = bars[-1].low if example['direction'] == 'BULLISH' else bars[-1].high
    return w


def aligned_rows(deviation):
    # Both completed anchor wicks have log length .01. Positive deviation is
    # C2 penetration; negative is a near miss. Prices are synthetic.
    return [candle(0, 102, 103, 100, 100*math.exp(.01)),
            candle(3600000, 102, 103, 100*math.exp(-.01*deviation),
                   100*math.exp(.01*(1-deviation))),
            candle(7200000, 101.5, 102, 100, 101.6)]


class BoundedContactGeometry(unittest.TestCase):
    def test_all_four_recorded_bad_alerts_fail_new_geometry_both_directions(self):
        for example in bad_examples():
            rows = [bot.Candle(*r) for r in example['bars']]
            for mirrored in (False, True):
                bars = list(map(reciprocal, rows)) if mirrored else rows
                side = example['direction']
                if mirrored:
                    side = 'BEARISH' if side == 'BULLISH' else 'BULLISH'
                tick = 1e-9 if mirrored else example['tick_size']
                args = (bars, side, tick, example['geometry_fraction'],
                        example['geometry_price_cap_pct'])
                with self.subTest(key=example['key'], mirrored=mirrored):
                    self.assertIsNotNone(bot.geometry(*args, c2_wick_contact=True,
                                                     c2_breach_fraction=None))
                    self.assertIsNone(bot.geometry(*args, c2_wick_contact=True))
                    plan = bot.make_plan(*args, c2_wick_contact=True)
                    tip = bars[-1].low if side == 'BULLISH' else bars[-1].high
                    self.assertTrue(plan is None or not plan.lower <= tip <= plan.upper)

    def test_original_qcom_stays_valid_and_has_a_bounded_discovery_zone(self):
        for bear in (False, True):
            rows = qcom_bars(bear)
            side = 'BEARISH' if bear else 'BULLISH'
            tick = .001 if bear else .01
            result = bot.geometry(rows, side, tick, .25, .15, c2_wick_contact=True)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result['wick_fraction'], .400395967, places=7)
            self.assertLessEqual(result['wick_fraction'], bot.C2_CONTACT_BREACH_FRACTION)
            self.assertLessEqual(result['gap_pct'], .15)
            w = live_watch(bear)
            self.assertTrue(w.ready(w.last_ms, .4))
            self.assertEqual(w.plan.c2_breach_fraction, .5)
            legacy = bot.make_plan(rows, side, tick, .25, .15,
                                   c2_wick_contact=True, c2_breach_fraction=None)
            self.assertGreaterEqual(w.plan.lower, legacy.lower)
            self.assertLessEqual(w.plan.upper, legacy.upper)

    def test_half_anchor_wick_limit_and_price_cap_are_independent(self):
        for bear in (False, True):
            side = 'BEARISH' if bear else 'BULLISH'
            for deviation, cap, accepted in ((.49, 1, True), (.51, 1, False),
                                              (.18, .15, False), (.18, .25, True),
                                              (-.30, 1, False)):
                rows = aligned_rows(deviation)
                if bear:
                    rows = list(map(reciprocal, rows))
                with self.subTest(bear=bear, deviation=deviation, cap=cap):
                    result = bot.geometry(rows, side, .001, .25, cap, c2_wick_contact=True)
                    self.assertEqual(result is not None, accepted)
                    plan = bot.make_plan(rows, side, .001, .25, cap, c2_wick_contact=True)
                    tip = rows[-1].high if bear else rows[-1].low
                    self.assertEqual(plan is not None and plan.lower <= tip <= plan.upper, accepted)

    def test_invalid_breach_limits_cannot_enable_unbounded_new_contact(self):
        rows = qcom_bars()
        for limit in (0, -.5, .51, math.inf, math.nan):
            for fn in (bot.geometry, bot.make_plan):
                self.assertIsNone(fn(rows, 'BULLISH', .01, .25, .15,
                                     c2_wick_contact=True, c2_breach_fraction=limit))


class BoundedContactLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_bad_alerts_cannot_pass_live_readiness_or_snapshot_delivery(self):
        for example in bad_examples():
            with self.subTest(key=example['key']), tempfile.TemporaryDirectory() as folder:
                cls = FuturesScanner if example['key'].startswith('SNDK') else bot.Scanner
                s = scanner(folder, cls)
                legacy = recorded_watch(example, legacy=True)
                self.assertTrue(legacy.ready(legacy.last_ms, .4))
                w = recorded_watch(example)
                if w is None:
                    continue
                self.assertFalse(w.ready(w.last_ms, .4))
                calls = []
                async def telegram(*args): calls.append(args)
                s.telegram_call = telegram
                with patch.object(s, 'approach_context', return_value={'ok':True}):
                    with self.assertRaisesRegex(ValueError, 'Geometry changed'):
                        await s.send_watch(w)
                self.assertFalse(calls)
                self.assertFalse(s.store.data['alerts'])

    async def test_qcom_delivery_persists_bounded_policy_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder, FuturesScanner)
            w = live_watch()
            calls = []
            async def telegram(method, payload):
                calls.append(method)
                return {'ok':True, 'result':{'message_id':123}}
            s.telegram_call = telegram
            with patch.object(s, 'quality_check', return_value={'ok':True, 'reason':'QUALITY_READY'}), \
                    patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                await s.send_watch(w)
                await s.send_watch(w)
            self.assertEqual(calls, ['sendMessage'])
            setup = s.store.data['alerts'][w.key]['setup']
            self.assertEqual(setup['geometry_c2_breach_fraction'], .5)
            original = deepcopy(setup)
            s.store.save()
            s = scanner(folder, FuturesScanner)
            s.tick_sizes[w.symbol] = .01
            async def bases(symbol): return w.bars
            s.fetch_symbol_bases = bases
            s.candles_for_timeframe = lambda data, tf: data if tf == '4H' else []
            with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                await s.scan_symbol(w.symbol)
            self.assertTrue(s.watches[w.key].alerted)
            self.assertEqual(s.watches[w.key].plan.c2_breach_fraction, .5)
            self.assertEqual(s.store.data['alerts'][w.key]['setup'], original)

    async def test_historical_verdicts_keep_old_policy_new_verdicts_use_bound(self):
        for example in bad_examples():
            for legacy in (False, True):
                with self.subTest(key=example['key'], legacy=legacy), tempfile.TemporaryDirectory() as folder:
                    cls = FuturesScanner if example['key'].startswith('SNDK') else bot.Scanner
                    s = scanner(folder, cls)
                    w = recorded_watch(example, legacy=True)
                    setup = s.stamp_setup(bot.make_setup(w, .1, True))
                    if legacy:
                        setup['strategy_version'] = '3.6.1'
                        setup.pop('geometry_c2_breach_fraction')
                    else:
                        setup['geometry_c2_breach_fraction'] = .5
                    frozen = deepcopy(setup)
                    s.store.data['alerts'][w.key] = dict(status='sent', setup=setup)
                    s.store.data['shadow_entries'][w.key] = dict(setup=deepcopy(setup),
                        baseline='TEST', first_quality={'reason':'TEST'}, hypothetical=True)
                    async def edit(*args): pass
                    s.edit_record = edit
                    await s.close_updates(w.symbol, w.timeframe, w.bars, w.bars[-1].close_time+1)
                    expected = 'CONFIRMED' if legacy else 'INVALID'
                    self.assertEqual(s.store.data['alerts'][w.key]['close_verdict'], expected)
                    self.assertEqual(s.store.data['shadow_entries'][w.key]['close_verdict'], expected)
                    self.assertEqual(s.store.data['alerts'][w.key]['setup'], frozen)

    async def test_restored_v361_record_keeps_explicit_legacy_policy(self):
        example = bad_examples()[-1]
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder, FuturesScanner)
            w = recorded_watch(example, legacy=True)
            setup = s.stamp_setup(bot.make_setup(w, .1, True))
            setup['strategy_version'] = '3.6.1'
            setup.pop('geometry_c2_breach_fraction')
            s.store.data['alerts'][w.key] = dict(status='sent', setup=deepcopy(setup))
            s.store.save()
            s = scanner(folder, FuturesScanner)
            s.tick_sizes[w.symbol] = .01
            async def bases(symbol): return w.bars
            s.fetch_symbol_bases = bases
            s.candles_for_timeframe = lambda data, tf: data if tf == '2H' else []
            with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
                await s.scan_symbol(w.symbol)
            self.assertTrue(s.watches[w.key].alerted)
            self.assertIsNone(s.watches[w.key].plan.c2_breach_fraction)
            self.assertEqual(s.store.data['alerts'][w.key]['setup'], setup)


if __name__ == '__main__':
    unittest.main()
