"""Causal entry proof, real quote-time study, and mocked production delivery."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ttw_entry as entry
import ttw_v3 as bot
from futures_feed import FuturesScanner
from test_ttw_v352 import scanner
from test_ttw_v37 import enable
from test_ttw_v371 import sndk_watch

M = entry.MINUTE


def event(start, o, h, l, c, symbol='SNDKUSDT', closed=True):
    return dict(s=symbol,E=start+M,k=dict(i='1m',x=closed,t=start,T=start+M-1,
                o=str(o),h=str(h),l=str(l),c=str(c),v='1'))


def feed(rows, symbol='SNDKUSDT'):
    book=entry.MinuteBook()
    for args in rows:
        data=event(*args,symbol=symbol)
        book.accept(data,data['E'])
    return book


def reflect(rows):
    return [(t,300-o,300-l,300-h,300-c) for t,o,h,l,c in rows]


DIRECT=[(0,104,106,103,105),(M,100.5,107,100,106.8)]
RETEST=[(0,109,110,108,109),(M,103,105,100,104),(2*M,104,104.5,101,102)]


class MinuteCoverage(unittest.TestCase):
    def test_only_closed_events_known_by_decision_are_usable(self):
        book=entry.MinuteBook();data=event(0,104,106,103,105,closed=False)
        self.assertFalse(book.accept(data,M));self.assertFalse(book.available('SNDKUSDT',M))
        data['k']['x']=True
        book.accept(data,M+10000)
        self.assertFalse(book.available('SNDKUSDT',M+1))
        self.assertEqual(len(book.available('SNDKUSDT',M+10000)),1)
        self.assertFalse(book.available('SNDKUSDT',4*M))

    def test_gap_and_changed_final_candle_invalidate_previous_generation(self):
        book=feed(DIRECT);generation=book.generations.get('SNDKUSDT',0)
        book.accept(event(4*M,104,106,103,105),5*M)
        self.assertEqual(len(book.rows['SNDKUSDT']),1)
        self.assertGreater(book.generations['SNDKUSDT'],generation)
        changed=event(4*M,104,107,103,105)
        book.accept(changed,5*M+1)
        self.assertNotIn('SNDKUSDT',book.rows)

    def test_old_duplicates_are_ignored_and_retention_is_bounded(self):
        book=feed(DIRECT)
        self.assertFalse(book.accept(event(0,104,106,103,105),3*M))
        for i in range(2,200):book.accept(event(i*M,104,106,103,105),(i+1)*M)
        self.assertEqual(len(book.rows['SNDKUSDT']),entry.CAPACITY)
        book.clear();self.assertFalse(book.rows)

    def test_invalid_native_interval_and_prices_are_rejected(self):
        for data in (event(1,104,106,103,105),event(0,104,102,103,105)):
            with self.assertRaises(ValueError):entry.MinuteBook().accept(data,M)


class TurnEvidence(unittest.TestCase):
    def assess(self, rows, *, bear=False, price=None, extreme_ms=M+10, now=None,
               allow_direct=True, quote_ms=None, state=None, book=None):
        book=book or feed(reflect(rows) if bear else rows)
        now=now or (rows[-1][0]+M+100)
        if price is None:price=193.1 if bear else 106.9
        return entry.turn_evidence(book,'SNDKUSDT','BEARISH' if bear else 'BULLISH',
            price,.01,M+5,200 if bear else 100,extreme_ms,now,state if state is not None else {},
            allow_direct=allow_direct,quote_ms=quote_ms)

    def test_decisive_rejection_and_retest_work_in_both_directions(self):
        for bear in (False,True):
            direct=self.assess(DIRECT,bear=bear)
            self.assertTrue(direct['ok'],direct);self.assertEqual(direct['route'],'DIRECT_REJECTION')
            retest=self.assess(RETEST,bear=bear,price=194.97 if bear else 105.03,allow_direct=False)
            self.assertTrue(retest['ok'],retest);self.assertEqual(retest['route'],'DEFENDED_RETEST')

    def test_colour_flicker_without_price_proof_is_held(self):
        rows=[DIRECT[0],(M,100.5,107,100,100.6)]
        self.assertFalse(self.assess(rows)['ok'])
        self.assertFalse(self.assess(DIRECT,allow_direct=False)['ok'])

    def test_new_extreme_and_price_return_reset_eligibility(self):
        self.assertFalse(self.assess(DIRECT,extreme_ms=2*M+50)['ok'])
        self.assertFalse(self.assess(DIRECT,price=105.9)['ok'])
        rows=DIRECT+[(2*M,106.8,106.9,105,105.5)]
        self.assertFalse(self.assess(rows,price=106.9)['ok'])

    def test_future_candle_or_quote_before_proof_cannot_create_entry(self):
        self.assertFalse(self.assess(DIRECT,now=2*M-1)['ok'])
        self.assertFalse(self.assess(DIRECT,quote_ms=2*M-1)['ok'])
        self.assertFalse(self.assess(DIRECT,quote_ms=2*M-5000)['ok'])
        self.assertFalse(self.assess(DIRECT,extreme_ms=4*M)['ok'])

    def test_retest_must_retrace_defend_and_break(self):
        self.assertFalse(self.assess(RETEST,price=105.01,allow_direct=False)['ok'])
        for bad in ((2*M,104,104.5,99.9,102),(2*M,104,105.5,101,102),
                    (2*M,104,104.5,103.9,104.1)):
            self.assertFalse(self.assess(RETEST[:2]+[bad],price=105.03,allow_direct=False)['ok'])

    def test_frozen_boundary_cannot_be_refitted_to_rescue_a_quote(self):
        state={};book=feed(DIRECT)
        first=self.assess(DIRECT,state=state,book=book)
        boundary=deepcopy(first['boundary'])
        book.accept(event(2*M,105.9,106,105.5,105.8),3*M)
        later=self.assess(DIRECT+[(2*M,105.9,106,105.5,105.8)],price=106.01,
                          state=state,book=book)
        self.assertFalse(later['ok']);self.assertEqual(state['boundary'],boundary)
        book.clear('SNDKUSDT')
        self.assertFalse(self.assess(DIRECT,state=state,book=book)['ok'])

    def test_anchor_fading_is_based_only_on_completed_c1_and_c2(self):
        w=sndk_watch();p=entry.anchor_pressure(w.bars,'BULLISH',.01)
        self.assertTrue(p['direct_route_eligible'])
        rows=w.bars.copy();rows[-1]=replace(rows[-1],open=2000,high=3000,low=1,close=2500)
        self.assertEqual(entry.anchor_pressure(rows,'BULLISH',.01),p)
        rows=w.bars.copy();rows[-2]=replace(rows[-2],close=rows[-2].open+1)
        self.assertFalse(entry.anchor_pressure(rows,'BULLISH',.01)['direct_route_eligible'])

    def test_closed_minute_cannot_hide_an_extreme_missing_from_trade_snapshot(self):
        rows=[DIRECT[0],(M,100.5,107,99.9,106.8)]
        r=self.assess(rows)
        self.assertFalse(r['ok']);self.assertEqual(r['reason'],'ENTRY_EXTREME_NOT_RECONCILED')


class RecordedDecisionStudy(unittest.TestCase):
    def test_recorded_sndk_rejection_stays_eligible_after_location_correction(self):
        r=json.loads((Path(__file__).parent/'fixtures/sndk_entry_turn_20261105.json').read_text())
        book=entry.MinuteBook()
        for row in r['minutes']:
            self.assertLess(row[6],r['observed_at_ms'])
            book.accept(event(row[0],*row[1:5]),row[6]+1)
        result=entry.turn_evidence(book,r['symbol'],r['direction'],r['entry'],r['tick_size'],
            r['touch_ms'],r['extreme'],r['extreme_known_ms'],r['observed_at_ms'],{})
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['route'],'DIRECT_REJECTION')

    def test_saved_winner_and_failed_quote_examples_use_no_future_minutes(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/entry_turn_decisions_20261105.json').read_text())
        counts=Counter()
        for r in fixture['examples']:
            book=entry.MinuteBook()
            for row in r['minutes']:
                self.assertLess(row[6],r['observed_at_ms'])
                book.accept(event(row[0],*row[1:5],symbol=r['key'].split('|')[0]),row[6]+1)
            # C3 here is irrelevant: only the completed C1/C2 pressure is read.
            anchors=[bot.Candle(**x) for x in r['anchor_bars']]
            pressure=entry.anchor_pressure(anchors+[anchors[-1]],r['direction'],r['tick_size'])
            result=entry.turn_evidence(book,r['key'].split('|')[0],r['direction'],r['entry'],
                r['tick_size'],r['touch_ms'],r['extreme'],r['extreme_known_ms'],
                r['observed_at_ms'],{},allow_direct=pressure['direct_route_eligible'])
            self.assertEqual(result['route'],r['expected_route'],r['key'])
            counts[(r['old_recorded_sl1_tp1'],result['ok'])]+=1
        self.assertEqual(counts[('TP',True)],1)
        self.assertEqual(counts[('STOP',False)],9)
        self.assertEqual(counts[('STOP',True)],2)
        self.assertEqual(sum(counts.values()),26)


class ProductionTurnIntegration(unittest.IsolatedAsyncioTestCase):
    def prepared(self,folder):
        s=enable(scanner(folder,FuturesScanner));s.cfg.entry_turn_mode='CONFIRM'
        w=sndk_watch();w.stream_epoch=s.stream_epoch
        first=(w.last_ms//M-2)*M
        w.extreme_ms=first+M+10
        s.entry_minutes=feed([(first,1001,1003,1000,1002),
                              (first+M,998,1005,997.14,1004.5)])
        return s,w

    async def test_missing_minutes_hold_and_keep_one_tracked_baseline_then_send_once(self):
        with tempfile.TemporaryDirectory() as folder:
            s,w=self.prepared(folder);minutes=s.entry_minutes;s.entry_minutes=entry.MinuteBook();calls=[]
            async def telegram(method,payload):
                calls.append((method,payload));return {'ok':True,'result':{'message_id':123}}
            s.telegram_call=telegram
            with patch.object(bot.time,'time',return_value=w.last_ms/1000):
                await s.send_watch(w);await s.send_watch(w)
                self.assertFalse(calls);self.assertFalse(w.alerted)
                baseline=[x for x in s.store.data['shadow_entries'].values()
                          if x['baseline']=='PRE_TURN_FULL_QUALITY_V38']
                self.assertEqual(len(baseline),1)
                self.assertEqual(baseline[0]['observed_at_ms'],w.last_ms)
                s.entry_minutes=minutes
                await s.send_watch(w);await s.send_watch(w)
            self.assertEqual(len(calls),1)
            r=s.store.data['alerts'][w.key]
            self.assertEqual(r['setup']['entry_turn']['route'],'DIRECT_REJECTION')
            self.assertIn('EARLY C3 · rejection',calls[0][1]['text'])
            s.accept_trade(w.symbol,1,w.last_ms+1,900)
            b=baseline[0]
            self.assertEqual(b['trade_tracking']['paths']['sl1']['tp1'],'STOP')

    async def test_price_turn_is_rechecked_after_chart_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            s,w=self.prepared(folder);s.cfg.send_charts=True;calls=[]
            async def render(*args):
                w.observe(w.extreme-.1,w.last_ms+1)
                w.observe(1010.59,w.last_ms+1)
                return b'png'
            async def photo(*args):calls.append(args)
            s.telegram_photo=photo
            with patch.object(bot.time,'time',side_effect=lambda:w.last_ms/1000),\
                 patch.object(bot.asyncio,'to_thread',side_effect=render):
                await s.send_watch(w)
            self.assertFalse(calls);self.assertFalse(s.store.data['alerts'])

    async def test_observation_mode_and_higher_frames_preserve_old_eligibility(self):
        with tempfile.TemporaryDirectory() as folder:
            s,w=self.prepared(folder);s.entry_minutes=entry.MinuteBook()
            s.cfg.entry_turn_mode='OBSERVE'
            r=s.entry_turn_check(w,w.last_ms)
            self.assertTrue(r['ok']);self.assertTrue(r['would_hold'])
            s.cfg.entry_turn_mode='CONFIRM';w.timeframe='1D'
            self.assertEqual(s.entry_turn_check(w,w.last_ms)['route'],'EXEMPT')
            self.assertIn('3.7.0',bot.COMPATIBLE_VERSIONS)

    async def test_runtime_config_enables_confirmation_with_explicit_rollback(self):
        with patch.dict(os.environ,{'TELEGRAM_CHAT_ID':'unit-test','TELEGRAM_BOT_TOKEN':'unit-test'}):
            self.assertEqual(bot.Config().entry_turn_mode,'CONFIRM')
            for mode in ('OFF','OBSERVE','CONFIRM'):
                with patch.dict(os.environ,{'V38_ENTRY_TURN_MODE':mode}):
                    self.assertEqual(bot.Config().entry_turn_mode,mode)
            with patch.dict(os.environ,{'V38_ENTRY_TURN_MODE':'invalid'}):
                with self.assertRaises(ValueError):bot.Config()

    async def test_stream_subscribes_to_minutes_and_routes_final_events_without_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            s,w=self.prepared(folder);s.entry_minutes=entry.MinuteBook();s.symbols=[w.symbol]
            paths=[];data=event(w.last_ms//M*M-M,1001,1003,1000,1002)
            class Socket:
                async def connect(self,url):paths.append(url)
                async def close(self):pass
                async def messages(self):
                    yield dict(data=dict(data,e='kline'))
                    self_result=len(s.entry_minutes.rows.get(w.symbol,()))
                    self.assertion_result=self_result
                    s.stop_event.set()
                    yield dict(data=dict(e='ignored'))
            socket=Socket()
            with patch.object(bot,'WebSocket',return_value=socket),\
                 patch.object(bot.time,'time',return_value=w.last_ms/1000),\
                 patch.object(bot.asyncio,'sleep',return_value=None):
                await s.stream_loop()
            self.assertEqual(socket.assertion_result,1)
            self.assertIn('sndkusdt@kline_1m',paths[0])
            self.assertEqual(s.stats['stream_entry_minute_events'],1)
            self.assertFalse(s.entry_minutes.rows)  # disconnect clears evidence


if __name__ == '__main__':unittest.main()
