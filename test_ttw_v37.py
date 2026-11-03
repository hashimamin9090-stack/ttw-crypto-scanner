"""Lesson-style synthetic cases plus recorded regressions; no live messages.

Synthetic candle strengths and swings test causal implementation, not a
calibrated strategy or exact OHLC reconstructed from the mentor's video.
"""
from copy import deepcopy
from dataclasses import replace
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import ttw_v3 as bot
import ttw_lesson as lesson
import ttw_context as context
import ttw_outcomes as outcomes
import three_tap
from test_ttw_v3 import candle, approach
from test_ttw_v33 import setup, structural_bars
from test_ttw_v352 import scanner
from test_ttw_v361 import live_watch as qcom_watch

D=3600000


def weak_rows(bear=False):
    rows=approach()+[candle(0,102,103,100,101),
                     candle(D,101,102,99,100.8),
                     candle(2*D,100,100.2,98.01,99.9)]
    return reflect(rows) if bear else rows


def reflect(rows):
    return [replace(b,open=220-b.open,high=220-b.low,low=220-b.high,close=220-b.close) for b in rows]


def enable(s,stop='SL1'):
    s.cfg.lesson_enabled=True;s.cfg.three_tap_enabled=True;s.cfg.required_stop_policy=stop
    return s


def swings(bearish=True):
    centres=[100,101,102,103,105,108,110,108,105,101,98,96,95,96,98,102,
             105,108,110,108,104,100,97,96,98,102,106,110]
    rows=[candle(i*D,p,p+.4,p-.4,p,D) for i,p in enumerate(centres)]
    return rows if bearish else reflect(rows)


class ExhaustionEvidence(unittest.TestCase):
    def test_weak_prior_colour_is_valid_exhaustion_in_both_directions(self):
        for bear in (False,True):
            rows=weak_rows(bear);side='BEARISH' if bear else 'BULLISH'
            r=lesson.exhaustion(rows,side,.01,closed=True)
            self.assertTrue(r['supports_reversal']);self.assertEqual(r['state'],'WEAK_CONTINUATION')
            self.assertTrue(bot.geometry(rows[-3:],side,.01,.25,.15))
            verdict=lesson.close_classification(rows,dict(direction=side,tick_size=.01),True)
            self.assertEqual(verdict['verdict'],'CONFIRMED')

    def test_strong_continuation_and_no_wick_are_not_exhaustion(self):
        rows=weak_rows();rows[-1]=replace(rows[-1],close=98.1)
        r=lesson.exhaustion(rows,'BULLISH',.01)
        self.assertEqual(r['state'],'CONTINUATION_PRESSURE');self.assertFalse(r['supports_reversal'])
        rows[-1]=replace(rows[-1],close=rows[-1].low)
        self.assertFalse(lesson.exhaustion(rows,'BULLISH',.01)['supports_reversal'])

    def test_large_opposing_body_is_not_rejected_by_wick_body_rule(self):
        rows=weak_rows();rows[-1]=replace(rows[-1],high=104,close=103.8)
        r=lesson.exhaustion(rows,'BULLISH',.01)
        self.assertTrue(r['supports_reversal']);self.assertEqual(r['state'],'REVERSAL_PRESSURE')
        self.assertLess(r['relevant_wick'],r['body'])

    def test_colour_flip_with_little_recovery_is_unresolved(self):
        rows=weak_rows();rows[-1]=replace(rows[-1],high=110,close=100.1)
        r=lesson.exhaustion(rows,'BULLISH',.01)
        self.assertFalse(r['supports_reversal']);self.assertEqual(r['state'],'UNRESOLVED')

    def test_geometry_failure_is_not_repaired_by_strength(self):
        rows=weak_rows()
        r=lesson.close_classification(rows,dict(direction='BULLISH',tick_size=.01),False)
        self.assertEqual(r['verdict'],'INVALID');self.assertEqual(r['reasons'],['GEOMETRY_FAILED'])

    def test_raw_scale_comparison_does_not_select_a_winner(self):
        rows=[candle(0,104,105,100,103),candle(D,96,97,90.5,95),candle(2*D,90,93,81,92)]
        r=lesson.alignment_comparison(rows,'BULLISH')
        self.assertAlmostEqual(r['linear_expected_c2'],90.5)
        self.assertAlmostEqual(r['log_expected_c2'],90)
        self.assertEqual(r['source_scale'],'LOG');self.assertEqual(r['policy'],'MEASUREMENT_ONLY')

    def test_compression_and_reaction_are_observations_not_vetoes(self):
        rows=[candle(i*D,100.1,100.3,99.98,100.02) for i in range(3)]
        r=lesson.level_reaction(rows,'BULLISH',100,.01,1)
        self.assertEqual(r['state'],'COMPRESSION');self.assertEqual(r['scope'],'OBSERVE_ONLY')
        rows[-1]=replace(rows[-1],high=101.2,close=101)
        self.assertEqual(lesson.level_reaction(rows,'BULLISH',100,.01,1)['state'],'REACTION')


class SwingObservations(unittest.TestCase):
    def test_distinct_swings_watch_then_resolve_only_when_observed(self):
        rows=swings();now=rows[-1].open_time+1000
        first=three_tap.scan(rows,.01,now)
        self.assertEqual(len(first),1);watch=first[0]
        self.assertEqual(watch['phase'],'THIRD_VISIT');self.assertFalse(watch['entry_eligible'])
        self.assertIsNone(watch['resolution']);self.assertLessEqual(watch['known_at_ms'],now)
        extended=rows+[candle(28*D,110,110.3,90,92),candle(29*D,92,93,91,92),candle(30*D,92,93,91,92)]
        resolved=next(r for r in three_tap.scan(extended,.01,30*D+1000) if r['identity']==watch['identity'])
        self.assertEqual(resolved['phase'],'RESOLVED')
        self.assertEqual(resolved['resolution']['status'],'LOWER_BOUNDARY_ACCEPTED')
        self.assertGreaterEqual(resolved['known_at_ms'],resolved['resolution']['known_at_ms'])
        self.assertEqual(watch['anchors'],resolved['anchors'])

    def test_future_bars_cannot_change_current_observation(self):
        rows=swings();now=rows[-1].open_time+1000
        before=three_tap.scan(rows,.01,now)
        future=rows+[candle(28*D,110,130,80,90),candle(29*D,90,120,70,75)]
        self.assertEqual(before,three_tap.scan(future,.01,now))

    def test_adjacent_flat_candles_and_gapped_data_cannot_supply_swings(self):
        flat=[candle(i*D,100,100.1,99.9,100) for i in range(35)]
        self.assertEqual(three_tap.scan(flat,.01,35*D),[])
        rows=swings();del rows[12]
        self.assertEqual(three_tap.scan(rows,.01,28*D),[])

    def test_mirrored_range_resolution_uses_real_opposite_boundary(self):
        rows=swings(False);watch=three_tap.scan(rows,.01,27*D+1000)[0]
        self.assertEqual(watch['side'],'LOW');self.assertIsNone(watch['resolution'])
        more=reflect(swings()+[candle(28*D,110,110.3,90,92),candle(29*D,92,93,91,92)])
        later=next(r for r in three_tap.scan(more,.01,30*D) if r['identity']==watch['identity'])
        self.assertEqual(later['resolution']['status'],'UPPER_BOUNDARY_ACCEPTED')

    def test_existing_range_does_not_move_its_zone_or_anchors(self):
        rows=swings();watch=three_tap.scan(rows,.01,27*D+1000)[0]
        near=rows+[candle(28*D,110,110.8,109,110.6)]
        tracked=three_tap.advance(watch,near,29*D)
        self.assertEqual(tracked['phase'],'THIRD_VISIT')
        self.assertEqual(tracked['zone'],watch['zone']);self.assertEqual(tracked['anchors'],watch['anchors'])
        later=near+[candle(29*D,110.6,113,110,112)]
        result=three_tap.advance(tracked,later,30*D)
        self.assertEqual(result['resolution']['status'],'UPPER_BOUNDARY_ACCEPTED')
        self.assertEqual(result['known_at_ms'],30*D)

    def test_missing_third_bar_cannot_manufacture_a_resolved_history(self):
        rows=swings();watch=three_tap.scan(rows,.01,27*D+1000)[0]
        incomplete=[candle(28*D,110,110.3,90,92)]
        result=three_tap.advance(watch,incomplete,29*D)
        self.assertIsNone(result['resolution']);self.assertEqual(result['resolution_coverage'],'UNKNOWN')


class StopPolicy(unittest.TestCase):
    def quality(self,policy,obstacle=105):
        s=setup();s['sl2']=95
        profile=context.timeframe_profile('1D');profile.update(required_stop_policy=policy,require_location=False)
        location=dict(ok=True,level=None,obstacle=dict(level=obstacle,distance=obstacle-s['entry']),setup_atr=2)
        with patch.object(context,'location_context',return_value=location):
            return context.entry_quality(structural_bars(),[],s,dict(ok=True,regime='ORDERLY_OR_RANGE'),
                                         precision=True,profile=profile)

    def test_tight_stop_can_qualify_while_alternative_is_explicitly_ineligible(self):
        self.assertFalse(self.quality('BOTH')['ok'])
        r=self.quality('SL1');self.assertTrue(r['ok']);self.assertTrue(r['stop_eligibility']['sl1']['eligible'])
        self.assertFalse(r['stop_eligibility']['sl2']['eligible'])

    def test_sl1_policy_cannot_skip_common_target_clearance(self):
        r=self.quality('SL1',102.95)
        self.assertFalse(r['ok']);self.assertEqual(r['reason'],'TARGET_BEYOND_OPPOSING_LEVEL')

    def test_invalid_policy_fails_closed(self):
        self.assertEqual(self.quality('INVALID')['reason'],'INVALID_STOP_POLICY')


class LessonIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_first_seen_close_colour_and_restart_are_honest(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows();now=rows[-1].open_time+1000
            s.scan_lesson_evidence('BTCUSDT','4H',rows,.01,now)
            key=lesson.candidate_id(s.market_name,'BTCUSDT','4H','BULLISH',rows[-1].open_time)
            r=s.store.data['lesson_candidates'][key]
            self.assertTrue(r['observed_before_close']);self.assertFalse(r['sequence_verified'])
            s.scan_lesson_evidence('BTCUSDT','4H',rows,.01,rows[-1].close_time+1)
            closed=s.store.data['lesson_candidates'][key]
            self.assertEqual(closed['verdict'],'CONFIRMED');self.assertEqual(closed['first_seen_ms'],now)
            self.assertEqual(s.store.data['alerts'],{})
            s.store.save();again=enable(scanner(folder))
            self.assertEqual(again.store.data['lesson_candidates'][key]['first_seen_ms'],now)
            self.assertTrue(again.store.data['lesson_candidates'][key]['context_gap'])
            again.scan_lesson_evidence('BTCUSDT','4H',rows,.01,rows[-1].close_time+1)
            self.assertFalse(again.store.data['lesson_candidates'][key]['context_gap'])

    async def test_closed_discovery_is_not_backdated_as_an_early_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows();now=rows[-1].close_time+1
            s.scan_lesson_evidence('BTCUSDT','4H',rows,.01,now)
            r=next(r for r in s.store.data['lesson_candidates'].values() if r['direction']=='BULLISH')
            self.assertFalse(r['observed_before_close']);self.assertEqual(r['first_seen_ms'],now)

    async def test_new_close_verdict_is_separate_from_stop_and_legacy_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows()
            base=dict(setup(),strategy_version='3.7.0',symbol='BTCUSDT',candle3_open_time=2*D,
                      candle3_close_time=3*D-1,geometry_fraction=.25,geometry_price_cap_pct=.15,
                      tick_size=.01,sl1=98.5,sl2=98,lesson_policy=lesson.POLICY)
            s.store.data['alerts']['new']=dict(setup=deepcopy(base),status='sent',message_id=1)
            old=deepcopy(base);old.pop('lesson_policy');old['strategy_version']='3.6.2'
            s.store.data['alerts']['old']=dict(setup=old,status='sent',message_id=2)
            async def edit(*args): pass
            s.edit_record=edit
            await s.close_updates('BTCUSDT','4H',rows,3*D)
            self.assertEqual(s.store.data['alerts']['new']['close_verdict'],'CONFIRMED')
            self.assertTrue(s.store.data['alerts']['new']['trade_stop_breached_at_c3'])
            self.assertEqual(s.store.data['alerts']['old']['close_verdict'],'INVALID')
            self.assertEqual(s.store.data['alerts']['new']['setup'],base)

    async def test_follow_through_does_not_retarget_or_change_frozen_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows();base=dict(setup(),symbol='BTCUSDT',
                lesson_policy=lesson.POLICY,candle3_open_time=2*D,candle3_close_time=3*D-1,tick_size=.01)
            record=dict(setup=deepcopy(base),status='sent',close_verdict='CONFIRMED',
                        trade_tracking=outcomes.start_tracking(base,2*D+1000))
            record['trade_tracking']['paths']['sl1']['tp1']='TP'
            s.store.data['alerts']['x']=record
            frozen=deepcopy(record['trade_tracking'])
            async def edit(*args): pass
            s.edit_record=edit
            c4=candle(3*D,99.9,103,99,102)
            await s.lesson_follow_updates('BTCUSDT','4H',rows+[c4],4*D-1)
            self.assertNotIn('follow_through',record)
            await s.lesson_follow_updates('BTCUSDT','4H',rows+[c4],4*D)
            self.assertEqual(record['follow_through']['status'],'EXTENDED')
            self.assertEqual(record['setup'],base);self.assertEqual(record['trade_tracking'],frozen)
            await s.lesson_follow_updates('BTCUSDT','4H',rows+[c4],4*D+1000)
            self.assertEqual(len(record['management_events']),1)

    async def test_parent_context_is_independent_and_cannot_reactivate_after_breach(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows();now=rows[-1].open_time+1000
            s.scan_lesson_evidence('BTCUSDT','1M',rows,.01,now)
            s.symbols=['BTCUSDT']
            parents=s.store.data['lesson_candidates']
            r=lesson.parent_context(parents,s.market_name,'BTCUSDT',D//2,'BEARISH',now,100)
            self.assertTrue(r['available']);self.assertFalse(r['entry_veto'])
            self.assertEqual(r['parents'][0]['relation'],'COUNTERTREND')
            boundary=next(iter(parents.values()))['invalidation']
            s.accept_trade('BTCUSDT',1,now+1,boundary-.01)
            s.accept_trade('BTCUSDT',2,now+2,100)
            self.assertFalse(lesson.parent_context(parents,s.market_name,'BTCUSDT',D//2,'BEARISH',now+2,100)['available'])

    async def test_same_calendar_timeframe_and_gapped_context_are_not_a_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows();now=rows[-1].open_time+1000
            s.scan_lesson_evidence('BTCUSDT','1M',rows,.01,now)
            parents=s.store.data['lesson_candidates']
            self.assertFalse(lesson.parent_context(parents,s.market_name,'BTCUSDT',D//2,'BULLISH',now,100,timeframe='1M')['available'])
            s.outcome_gap('BTCUSDT',now,'TEST_GAP')
            self.assertFalse(lesson.parent_context(parents,s.market_name,'BTCUSDT',D//2,'BULLISH',now,100)['available'])

    async def test_follow_edit_retries_without_duplicating_the_management_event(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));rows=weak_rows()
            base=dict(setup(),symbol='BTCUSDT',lesson_policy=lesson.POLICY,
                      candle3_open_time=2*D,candle3_close_time=3*D-1,tick_size=.01)
            record=dict(setup=base,status='sent',close_verdict='CONFIRMED',
                        trade_tracking=outcomes.start_tracking(base,2*D+1000))
            s.store.data['alerts']['x']=record;calls=[]
            async def edit(*args):
                calls.append(args)
                if len(calls)==1:raise RuntimeError('temporary delivery error')
            s.edit_record=edit;c4=candle(3*D,99.9,103,99,102)
            await s.lesson_follow_updates('BTCUSDT','4H',rows+[c4],4*D)
            self.assertNotIn('follow_update_sent',record)
            await s.lesson_follow_updates('BTCUSDT','4H',rows+[c4],4*D+1)
            self.assertTrue(record['follow_update_sent']);self.assertEqual(len(calls),2)
            self.assertEqual(len(record['management_events']),1)

    async def test_recorded_qcom_keeps_the_live_trigger_and_has_an_early_label(self):
        from futures_feed import FuturesScanner
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder,FuturesScanner));w=qcom_watch();now=w.last_ms
            before=w.ready(now,.4);self.assertTrue(before)
            q=dict(ok=True,reason='QUALITY_READY',stop_eligibility=dict(sl1=dict(eligible=True),sl2=dict(eligible=False)))
            p=s.stamp_setup(bot.make_setup(w,.1,True));s.annotate_lesson_setup(p,w,q,now)
            self.assertEqual(w.ready(now,.4),before)
            text,_=bot.alert_payload(p,w.key)
            self.assertIn('EARLY C3',text);self.assertIn('reference only',text)

    async def test_lesson_command_preserves_existing_recipient_access(self):
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder));calls=[]
            async def telegram(method,payload):calls.append(payload)
            s.telegram_call=telegram
            await s.handle_message(dict(text='/lesson',chat=dict(id='stranger')))
            self.assertEqual(calls,[])
            await s.handle_message(dict(text='/lesson',chat=dict(id=s.chat_id)))
            self.assertEqual(len(calls),1);self.assertIn('not win rates',calls[0]['text'])

    async def test_delivery_persists_lesson_policy_and_short_caption(self):
        from futures_feed import FuturesScanner
        with tempfile.TemporaryDirectory() as folder:
            s=enable(scanner(folder,FuturesScanner));w=qcom_watch();now=w.last_ms;calls=[]
            q=dict(ok=True,reason='QUALITY_READY',stop_eligibility=dict(sl1=dict(eligible=True),sl2=dict(eligible=False)))
            async def telegram(method,payload):
                calls.append(payload);return dict(ok=True,result=dict(message_id=1))
            s.telegram_call=telegram
            with patch.object(s,'quality_check',return_value=q),patch.object(s,'approach_context',return_value=dict(ok=True)),patch.object(bot.time,'time',return_value=now/1000):
                await s.send_watch(w)
            self.assertEqual(len(calls),1);self.assertLessEqual(len(calls[0]['text']),1024)
            self.assertIn('EARLY C3',calls[0]['text']);self.assertIn('reference only',calls[0]['text'])
            saved=s.store.data['alerts'][w.key]['setup']
            self.assertEqual(saved['lesson_policy'],lesson.POLICY)
            self.assertEqual(saved['required_stop_policy'],'SL1')
            self.assertEqual(saved['source_identity']['market'],'BINANCE_FUTURES')
            self.assertEqual(saved['source_identity']['price_scale'],'LOG')


class LimitsAndConfig(unittest.TestCase):
    def test_candidate_retention_is_bounded(self):
        rows={}
        for i in range(600):
            lesson.retain_candidate(rows,str(i),dict(observed_at_ms=i,phase='LIVE',close_ms=1000))
        self.assertEqual(len(rows),lesson.MAX_CANDIDATES)
        self.assertNotIn('0',rows);self.assertIn('599',rows)

    def test_production_defaults_enable_observations_and_tight_stop_policy(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{
                'TELEGRAM_BOT_TOKEN':'unit-test','TELEGRAM_CHAT_ID':'unit-test','STATE_DIR':folder},clear=True):
            cfg=bot.Config()
            self.assertTrue(cfg.lesson_enabled);self.assertTrue(cfg.three_tap_enabled)
            self.assertEqual(cfg.required_stop_policy,'SL1')
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'unit-test','TELEGRAM_CHAT_ID':'unit-test','V37_REQUIRED_STOP':'unsafe'},clear=True):
            with self.assertRaises(ValueError):bot.Config()


if __name__=='__main__':
    unittest.main()
