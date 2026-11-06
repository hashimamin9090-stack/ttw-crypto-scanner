"""Narrow swept-anchor alternative, recorded quote study, and scanner wiring."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import ttw_entry as entry
import ttw_v3 as bot
from test_ttw_v352 import scanner
from test_ttw_v38 import event


FIXTURES = Path(__file__).parent/'fixtures'


def recorded():
    return json.loads((FIXTURES/'tao_sweep_reclaim_20261106.json').read_text())


def inputs(r):
    book = entry.MinuteBook()
    for b in r['minutes']:
        book.accept(event(b['open_time'],b['open'],b['high'],b['low'],b['close'],
                          symbol=r['symbol']),b['close_time']+1)
    return book, [bot.Candle(**b) for b in r['anchor_bars']]


def assess(r, **overrides):
    book, anchors = inputs(r)
    args = dict(quote_ms=r['observed_at_ms'],allow_direct=False,anchor_bars=anchors)
    args.update(overrides)
    return entry.turn_evidence(book,r['symbol'],r['direction'],r['entry'],r['tick_size'],
        r['touch_ms'],r['extreme'],r['extreme_known_ms'],r['observed_at_ms'],{},**args)


def mirror(r):
    r=deepcopy(r);r['direction']='BEARISH';r['entry']=500-r['entry'];r['extreme']=500-r['extreme']
    for b in r['anchor_bars']+r['minutes']:
        b.update(open=500-b['open'],high=500-b['low'],low=500-b['high'],close=500-b['close'])
    return r


class SweptAnchorProof(unittest.TestCase):
    def test_reject_persists_historical_event_without_reserved_field_collision(self):
        r=recorded();_,bars=inputs(r);bars.append(bot.Candle(**r['c3_at_quote']))
        plan=bot.make_plan(bars,'BULLISH',.1,.25,.15)
        with tempfile.TemporaryDirectory() as folder:
            s=scanner(folder)
            watch=bot.Watch(r['key'],r['symbol'],'2H',bars,plan)
            watch.failure_evidence=dict(reason='IMPULSE_BEFORE_TOUCH',event_ms=123,
                event_price=217.,strategy_version='old',close_time=1)
            s.watches[watch.key]=watch
            s.reject(watch,'IMPULSE_BEFORE_TOUCH')
            saved=s.store.data['sequence_rejections'][watch.key]
            self.assertEqual(saved['reason'],'IMPULSE_BEFORE_TOUCH')
            self.assertEqual(saved['event_ms'],123);self.assertEqual(saved['event_price'],217.)
            self.assertEqual(saved['strategy_version'],bot.VERSION)
            self.assertEqual(saved['close_time'],bars[-1].close_time)
            self.assertNotIn(watch.key,s.watches)

    def test_recorded_tao_reclaims_c2_and_frozen_micro_boundary(self):
        r=recorded();book,bars=inputs(r)
        self.assertFalse(entry.anchor_pressure(bars+[bars[-1]],r['direction'],.1)['direct_route_eligible'])
        self.assertFalse(assess(r,anchor_bars=None)['ok'])
        proof=assess(r)
        self.assertTrue(proof['ok'],proof);self.assertEqual(proof['route'],'SWEEP_RECLAIM')
        self.assertAlmostEqual(proof['trigger'],217.9)
        self.assertLess(proof['known_at_ms'],r['observed_at_ms'])
        self.assertEqual(proof['evidence'][0]['candle']['open_time'],1793945700000)
        self.assertEqual(r['hypothetical_original_sl1_tp1'],'TP')

    def test_symmetric_synthetic_bearish_sweep(self):
        result=assess(mirror(recorded()))
        self.assertTrue(result['ok'],result);self.assertEqual(result['route'],'SWEEP_RECLAIM')
        self.assertAlmostEqual(result['trigger'],282.1)

    def test_must_sweep_both_anchors_and_reclaim_c2_by_two_ticks(self):
        for bear in (False,True):
            r=mirror(recorded()) if bear else recorded()
            book,bars=inputs(r)
            # One completed anchor extends past C3: an ordinary bounce cannot
            # use this route even though the other anchor was swept.
            if bear:bars[0]=replace(bars[0],high=r['extreme']+.1)
            else:bars[0]=replace(bars[0],low=r['extreme']-.1)
            self.assertFalse(assess(r,anchor_bars=bars)['ok'])
            r['entry']=282.11 if bear else 217.89
            self.assertFalse(assess(r)['ok'])

    def test_later_close_below_reclaimed_wick_invalidates_proof_in_both_directions(self):
        for bear in (False,True):
            r=recorded()
            r['minutes'][-1].update(open=218.1,high=218.2,low=217.8,close=217.8)
            result=assess(mirror(r) if bear else r)
            self.assertFalse(result['ok'],result)

    def test_no_proof_before_reversal_close_and_no_stale_quote_or_anchor(self):
        r=recorded();r['observed_at_ms']=1793945759999
        r['minutes']=[b for b in r['minutes'] if b['close_time']<r['observed_at_ms']]
        self.assertFalse(assess(r)['ok'])
        r=recorded()
        self.assertFalse(assess(r,quote_ms=r['observed_at_ms']-3001)['ok'])
        _,bars=inputs(r)
        bars[-1]=replace(bars[-1],close_time=r['touch_ms'])
        self.assertFalse(assess(r,anchor_bars=bars)['ok'])

    def test_saved_26_quote_decisions_do_not_admit_additional_stopout_quotes(self):
        fixture=json.loads((FIXTURES/'entry_turn_decisions_20261105.json').read_text())
        counts=Counter()
        for r in fixture['examples']:
            book=entry.MinuteBook();symbol=r['key'].split('|')[0]
            for row in r['minutes']:
                self.assertLess(row[6],r['observed_at_ms'])
                book.accept(event(row[0],*row[1:5],symbol=symbol),row[6]+1)
            bars=[bot.Candle(**b) for b in r['anchor_bars']]
            pressure=entry.anchor_pressure(bars+[bars[-1]],r['direction'],r['tick_size'])
            result=entry.turn_evidence(book,symbol,r['direction'],r['entry'],r['tick_size'],
                r['touch_ms'],r['extreme'],r['extreme_known_ms'],r['observed_at_ms'],{},
                allow_direct=pressure['direct_route_eligible'],anchor_bars=bars)
            self.assertEqual(result['route'],r['expected_route'],r['key'])
            counts[(r['old_recorded_sl1_tp1'],result['ok'])]+=1
        self.assertEqual(counts[('STOP',False)],9);self.assertEqual(counts[('STOP',True)],2)
        self.assertEqual(counts[('TP',True)],1);self.assertEqual(sum(counts.values()),26)

    def test_scanner_uses_live_route_and_alert_caption_identifies_proof(self):
        r=recorded();book,bars=inputs(r);bars.append(bot.Candle(**r['c3_at_quote']))
        plan=bot.make_plan(bars,'BULLISH',.1,.25,.15)
        self.assertIsNotNone(plan)
        watch=bot.Watch(r['key'],r['symbol'],'2H',bars,plan)
        watch.last_ms=watch.verified_ms=r['observed_at_ms'];watch.last_price=r['entry']
        watch.touch_ms=r['touch_ms'];watch.extreme=r['extreme'];watch.extreme_ms=r['extreme_known_ms']
        with tempfile.TemporaryDirectory() as folder:
            s=scanner(folder);s.cfg.quality_enabled=True;s.cfg.entry_turn_mode='CONFIRM';s.entry_minutes=book
            proof=s.entry_turn_check(watch,r['observed_at_ms'])
            self.assertTrue(proof['ok'],proof);self.assertEqual(proof['route'],'SWEEP_RECLAIM')
            setup=bot.make_setup(watch,s.cfg.stop_buffer_pct,True)
            setup.update(entry_stage='EARLY_C3',entry_turn=proof)
            text,_=bot.alert_payload(setup,r['key'])
            self.assertIn('EARLY C3 · sweep reclaim',text)


if __name__ == '__main__':unittest.main()
