import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import ttw_context as context
import ttw_outcomes as outcomes
import ttw_v3 as bot
from test_ttw_v3 import approach, candle, fixture
import test_ttw_v33 as legacy
from test_ttw_v33 import history, setup, structural_bars


def mirror(bars):
    return [replace(b, open=200-b.open, high=200-b.low,
                    low=200-b.high, close=200-b.close) for b in bars]


def weak_approach():
    prior = [candle(-10800000+i*3600000, 102.005-i*.001,
                    103.004-i*.001, 100.004-i*.001, 102.004-i*.001)
             for i in range(3)]
    anchors = fixture()
    anchors[0] = replace(anchors[0], open=102.002, close=102.001)
    anchors[1] = replace(anchors[1], open=102.001, high=103, close=102)
    return prior+anchors


class TimeframeProfiles(unittest.TestCase):
    def test_all_supported_timeframes_have_exactly_two_profiles(self):
        intraday = {'2H', '3H', '4H', '6H', '8H', '12H', '16H'}
        for tf in bot.TIMEFRAMES:
            p = context.timeframe_profile(tf)
            self.assertEqual(p['name'], 'INTRADAY' if tf in intraday else 'HIGHER')
            self.assertEqual(p['require_location'], tf in intraday)
            self.assertEqual(p['btc_expansion_veto'], tf in intraday)

    def test_unknown_timeframe_cannot_inherit_relaxed_rules(self):
        for tf in ('24H', 'typo', '', None):
            with self.assertRaises(ValueError):
                context.timeframe_profile(tf)

    def test_intraday_reclaim_is_firmer_and_higher_reclaim_is_preserved(self):
        bars = fixture()
        for side, rows in (('BULLISH', bars), ('BEARISH', mirror(bars))):
            p = context.timeframe_profile('4H')
            needed = context.reclaim_distance(rows, side, .001, p['reclaim_fraction'],
                range_fraction=p['reclaim_range_fraction'], ticks=p['reclaim_ticks'])
            self.assertGreater(needed, context.reclaim_distance(rows, side, .001))
            h = context.timeframe_profile('1W')
            self.assertAlmostEqual(context.reclaim_distance(rows, side, .001, h['reclaim_fraction'],
                range_fraction=h['reclaim_range_fraction'], ticks=h['reclaim_ticks']),
                context.reclaim_distance(rows, side, .001))

    def test_three_tick_reclaim_fits_floor_without_allowing_full_impulse(self):
        bars = [candle(0,100,100.003,99.994,99.999),
                candle(3600000,100,100.004,99.996,99.999),
                candle(7200000,100,100.003,99.998,100.003)]
        for side, rows in (('BULLISH', bars), ('BEARISH', mirror(bars))):
            p = context.timeframe_profile('2H')
            plan = bot.make_plan(rows, side, .001, price_cap_pct=.1)
            self.assertIsNotNone(plan)
            self.assertAlmostEqual(plan.impulse_distance, .008)
            w = bot.Watch('micro', 'TESTUSDT', '2H', rows, plan)
            w.verified_ms = 7200000
            tip = rows[-1].low if side == 'BULLISH' else rows[-1].high
            w.observe(tip, 7200001)
            w.observe(rows[-1].open + (.003 if side == 'BULLISH' else -.003), 7200002)
            self.assertFalse(w.ready(7200002, .25))
            self.assertTrue(w.ready(7200002, p['max_entry_move_fraction']))
            needed = context.reclaim_distance(rows, side, .001, p['reclaim_fraction'],
                range_fraction=p['reclaim_range_fraction'], ticks=p['reclaim_ticks'])
            self.assertLessEqual(needed, plan.impulse_distance*p['max_entry_move_fraction'])
            w.observe(rows[-1].open + (.009 if side == 'BULLISH' else -.009), 7200003)
            self.assertEqual(w.failed, 'IMPULSE_ALREADY_AFTER_TOUCH')

    def test_small_approach_is_rejected_intraday_but_original_rule_is_preserved(self):
        for side, rows in (('BULLISH', weak_approach()), ('BEARISH', mirror(weak_approach()))):
            self.assertTrue(bot.reversal_context(rows, side, .001)['ok'])
            r = bot.reversal_context(rows, side, .001, .25)
            self.assertEqual(r['reason'], 'INTRADAY_APPROACH_TOO_SMALL')

    def test_genuine_approach_allows_both_directions_and_c1_colours(self):
        for reverse in (False, True):
            rows = approach()+fixture()
            if reverse:
                rows[-3] = replace(rows[-3], open=100.3, close=101)
            for side, bars in (('BULLISH', rows), ('BEARISH', mirror(rows))):
                self.assertTrue(bot.reversal_context(bars, side, .001, .25)['ok'])

    def test_c3_cannot_manufacture_a_stronger_approach(self):
        rows = weak_approach()
        before = bot.reversal_context(rows, 'BULLISH', .001, .25)
        rows[-1] = replace(rows[-1], high=200, low=1, close=150)
        self.assertEqual(bot.reversal_context(rows, 'BULLISH', .001, .25), before)

    def test_measurable_anchor_pullback_does_not_require_two_large_bodies(self):
        prior = [candle(t,104,105,103,104) for t in (-10800000,-7200000,-3600000)]
        anchors = [candle(0,102,103,100,101.99),
                   candle(3600000,101.99,102,100.5,101.2),
                   candle(7200000,101.2,101.3,101.0025,101.3)]
        for side, rows in (('BULLISH',prior+anchors),('BEARISH',mirror(prior+anchors))):
            r = bot.reversal_context(rows,side,.001,.25)
            self.assertTrue(r['ok'],r)
            self.assertEqual(r['approach_mode'],'C1_C2_PULLBACK')


class ProfileLocationAndRisk(unittest.TestCase):
    def bars(self):
        rows = structural_bars()
        rows[:-3] = [replace(b, open=101, close=101) for b in rows[:-3]]
        return rows

    def quality(self, rows, s, tf, btc=None):
        s = dict(s, timeframe=tf)
        return context.entry_quality(rows, [], s,
            btc or {'ok':True, 'regime':'ORDERLY_OR_RANGE'}, precision=True,
            profile=context.timeframe_profile(tf))

    def test_bounded_zone_contact_and_shallow_sweep_reclaim_both_directions(self):
        for tip, kind in ((99, 'ZONE_CONTACT'), (98.5, 'BOUNDED_SWEEP_RECLAIM')):
            rows = self.bars(); rows[-1] = replace(rows[-1], low=tip)
            for side, bars, price, extreme in (
                    ('BULLISH', rows, 99.9, tip),
                    ('BEARISH', mirror(rows), 100.1, 200-tip)):
                r = context.location_context(bars, [], side, price, extreme, .01,
                                             strong=True, interaction=True)
                self.assertTrue(r['ok'], r)
                self.assertEqual(r['level']['interaction'], kind)

    def test_old_proximity_without_zone_interaction_does_not_qualify(self):
        rows = self.bars(); rows[-1] = replace(rows[-1], low=98.2)
        self.assertTrue(context.location_context(rows, [], 'BULLISH', 99.9, 98.2, .01,
                                                strong=True)['ok'])
        self.assertFalse(context.location_context(rows, [], 'BULLISH', 99.9, 98.2, .01,
                                                 strong=True, interaction=True)['ok'])

    def test_deep_sweep_is_not_repaired_by_reclaim(self):
        rows = self.bars(); rows[-1] = replace(rows[-1], low=97)
        self.assertFalse(context.location_context(rows, [], 'BULLISH', 99.9, 97, .01,
                                                 strong=True, interaction=True)['ok'])

    def test_higher_setup_needs_no_matching_level_but_intraday_does(self):
        rows = self.bars()
        rows[:-3] = [replace(b, open=100, close=100, high=110, low=90) for b in rows[:-3]]
        for bear in (False, True):
            bars, s = mirror(rows) if bear else rows, setup()
            if bear:
                s['direction'] = 'BEARISH'
                for k in ('entry','sl1','sl2','tp1','tp2','wick3'):
                    s[k] = 200-s[k]
            self.assertEqual(self.quality(bars, s, '4H')['reason'], 'NO_ESTABLISHED_LEVEL')
            r = self.quality(bars, s, '1W')
            self.assertTrue(r['ok'], r)
            self.assertIsNone(r['location']['level'])

    def test_unknown_or_opposing_btc_blocks_only_intraday(self):
        rows, s = self.bars(), setup()
        for btc, reason in (({'ok':False, 'regime':'UNKNOWN'}, 'BTC_DATA_UNAVAILABLE'),
                            ({'ok':True, 'regime':'DOWN_EXPANSION'}, 'BTC_OPPOSING_EXPANSION')):
            self.assertEqual(self.quality(rows, s, '4H', btc)['reason'], reason)
            self.assertTrue(self.quality(rows, s, '1W', btc)['ok'])

    def test_higher_still_rejects_insufficient_reclaim_and_unusable_stops(self):
        rows, s = self.bars(), setup()
        self.assertEqual(self.quality(rows, dict(s,entry=99.701), '1W')['reason'], 'RECLAIM_TOO_SMALL')
        self.assertEqual(self.quality(rows, dict(s,sl2=95), '1W')['reason'], 'C2_TARGET_TOO_SMALL_FOR_RISK')
        self.assertEqual(self.quality(rows, dict(s,sl2=101), '1W')['reason'], 'C2_STOP_NOT_PROTECTIVE')

    def test_higher_still_checks_major_opposition_before_tp1(self):
        rows, s = self.bars(), setup()
        rows[:-3] = [replace(b, open=104.5, close=104.5, high=110, low=99) for b in rows[:-3]]
        self.assertEqual(self.quality(rows, dict(s,tp1=111), '1W')['reason'], 'TARGET_BEYOND_OPPOSING_LEVEL')

    def test_higher_btc_context_is_completed_causal_and_never_supportive_when_unknown(self):
        rows = history(24, 604800000)
        rows[-1] = replace(rows[-1], open=100, close=100.8)
        before = context.higher_btc_context(rows, '1W', 0)
        live = candle(0,100,1000,1,500,604800000)
        self.assertEqual(context.higher_btc_context(rows+[live], '1W', 0), before)
        self.assertEqual(before['trend'], 'UP')
        self.assertEqual(before['scope'], 'CONTEXT_ONLY')
        missing = context.higher_btc_context([], '1W', 0)
        self.assertFalse(missing['ok'])
        self.assertEqual(missing['trend'], 'UNKNOWN')
        self.assertFalse(context.higher_btc_context(rows, '1W', 2*604800000)['ok'])


class ProfileDelivery(unittest.IsolatedAsyncioTestCase):
    def scanner(self, folder):
        scanner = legacy.QualityIntegration().scanner(folder)
        scanner.cfg.precision_enabled = True
        scanner.stream_connected = True
        return scanner

    async def deliver(self, scanner, watch, now):
        calls = []
        async def telegram(method, payload):
            calls.append(method)
            return {'ok':True,'result':{'message_id':123}}
        scanner.telegram_call = telegram
        scanner.symbol_last_id[watch.symbol] = 10
        with patch.object(bot.time, 'time', return_value=now/1000):
            await scanner.send_watch(watch)
        return calls

    async def test_real_profile_delivery_both_directions_daily_weekly_and_intraday(self):
        for tf in ('2H','4H','12H','16H','1D','1W'):
            duration = int(tf[:-1])*(3600000 if tf.endswith('H') else 86400000 if tf.endswith('D') else 604800000)
            for bear in (False,True):
                with tempfile.TemporaryDirectory() as folder:
                    scanner = self.scanner(folder)
                    w = legacy.QualityIntegration().qualifying_watch(bear)
                    if tf in ('1D','1W'):
                        w.bars[:-6] = [replace(b, open=100, close=100, high=120, low=80) for b in w.bars[:-6]]
                    w.timeframe = tf
                    w.bars = [replace(b,open_time=b.open_time//3600000*duration,
                                      close_time=(b.open_time//3600000+1)*duration-1) for b in w.bars]
                    now = 2*duration+4000
                    w.key = 'TESTUSDT|'+tf+'|'+w.plan.direction+'|'+str(2*duration)
                    w.verified_ms = 2*duration; w.touch_ms = 2*duration+1000; w.last_ms = now
                    w.plan = replace(w.plan, price_cap_pct=.1)
                    if tf.endswith('H'):
                        scanner.btc_bars = {'15m':history(24,end=now//900000*900000)+[
                            candle(now//900000*900000,100,100.2,99.8,100,900000)],
                            '1h':history(24,3600000,end=now//3600000*3600000)}
                        scanner.last_quotes['BTCUSDT'] = (now,100)
                    calls = await self.deliver(scanner, w, now)
                    self.assertTrue(w.alerted, (tf,bear,dict(scanner.stats)))
                    self.assertEqual(len(calls),1)
                    r = scanner.store.data['alerts'][w.key]
                    self.assertEqual(r['timeframe_profile'], 'INTRADAY' if tf.endswith('H') else 'HIGHER')
                    self.assertIn('risk_checks', r['entry_quality'])
                    if tf in ('1D','1W'):
                        self.assertIsNone(r['entry_quality']['location']['level'])
                        self.assertEqual(r['entry_quality']['btc']['trend'], 'UNKNOWN')
                    self.assertEqual(await self.deliver(scanner,w,now), [])

    async def test_intraday_small_approach_is_retained_and_observed_without_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            rows = weak_approach()
            rows[-1] = replace(rows[-1], open=98.6, close=98.63)
            plan = bot.make_plan(rows[-3:], 'BULLISH', .001, price_cap_pct=.1)
            w = bot.Watch('weak', 'TESTUSDT', '4H', rows, plan)
            w.verified_ms = 7200000
            w.observe(98.01,7201000)
            w.observe(98.63,7204000)
            self.assertTrue(w.ready(7204000))
            self.assertEqual(await self.deliver(scanner,w,7204000), [])
            self.assertFalse(w.failed)
            self.assertEqual(w.quality_reason, 'INTRADAY_APPROACH_TOO_SMALL')
            self.assertIn(w.key, scanner.store.data['shadow_entries'])

    async def test_profiles_do_not_bypass_freshness_or_render_recheck(self):
        for tf in ('4H','1W'):
            with tempfile.TemporaryDirectory() as folder:
                scanner = self.scanner(folder); scanner.cfg.send_charts = True
                w = legacy.QualityIntegration().qualifying_watch(); w.timeframe = tf
                legacy.QualityIntegration().seed_btc(scanner)
                async def render_thread(*args):
                    w.last_ms -= 4000
                    return b'chart'
                with patch.object(bot.asyncio,'to_thread',render_thread):
                    self.assertEqual(await self.deliver(scanner,w,7204000), [])
                self.assertFalse(w.alerted)

    async def test_legacy_version_and_existing_alert_identity_are_preserved(self):
        self.assertIn('3.3.1',bot.COMPATIBLE_VERSIONS)
        self.assertEqual(bot.VERSION,'3.4.0')
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.scanner(folder)
            s = dict(setup(), strategy_version='3.3.1')
            scanner.store.data['alerts']['old'] = dict(setup=s,status='sent',
                trade_tracking=outcomes.start_tracking(s,8000000,10))
            scanner.store.save()
            reloaded = self.scanner(folder)
            self.assertIn('old',reloaded.store.data['alerts'])
            self.assertEqual(reloaded.store.data['alerts']['old']['setup']['strategy_version'],'3.3.1')
            self.assertTrue(reloaded.store.data['alerts']['old']['trade_tracking']['gap'])


class OutcomeEditRace(unittest.IsolatedAsyncioTestCase):
    async def test_second_stop_during_edit_is_logged_and_saved_even_if_edit_fails(self):
        for fail_edit in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                scanner = legacy.QualityIntegration().scanner(folder)
                s = dict(setup(),strategy_version=bot.VERSION,sl1=98,sl2=99)
                r = dict(setup=s,status='sent',timeframe_profile='INTRADAY',
                         trade_tracking=outcomes.start_tracking(s,8000000,10))
                scanner.store.data['alerts']['race'] = r
                scanner.outcome_keys[s['symbol']] = {'race'}
                self.assertTrue(outcomes.observe(r,98.5,8000100,11))
                scanner.dirty_outcomes.add('race')
                calls = []
                async def edit(key,record,status):
                    calls.append(status)
                    if len(calls) == 1:
                        await asyncio.sleep(0)
                        self.assertTrue(outcomes.observe(record,97.5,8000200,12))
                        scanner.dirty_outcomes.add(key)
                        if fail_edit:
                            raise RuntimeError('temporary Telegram failure')
                scanner.edit_record = edit
                await scanner.flush_outcomes(8000100)
                self.assertIn('race',scanner.dirty_outcomes)
                await scanner.flush_outcomes(8000300)
                self.assertNotIn('race',scanner.dirty_outcomes)
                logs = [json.loads(line) for line in (Path(folder)/'outcomes-v33.jsonl').read_text().splitlines()]
                self.assertEqual([v['revision'] for v in logs],[1,2])
                self.assertEqual(logs[-1]['paths']['sl1']['tp1'],'STOP')
                self.assertEqual(logs[-1]['paths']['sl2']['tp1'],'STOP')
                self.assertEqual(logs[-1]['timeframe_profile'],'INTRADAY')
                saved = json.loads((Path(folder)/'state-v2.json').read_text())
                self.assertEqual(saved['alerts']['race']['trade_tracking']['logged_revision'],2)
                self.assertEqual(saved['alerts']['race']['trade_tracking']['paths']['sl1']['tp1'],'STOP')


if __name__ == '__main__':
    unittest.main()
