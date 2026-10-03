import asyncio
import tempfile
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import TTW_BOT_V2 as bot


def candle(t, o, h, l, c, duration=1000):
    return bot.Candle(t, o, h, l, c, 1, t + duration - 1)


def mirror(c):
    return bot.Candle(c.open_time, 200-c.open, 200-c.low, 200-c.high,
                      200-c.close, c.volume, c.close_time)


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    def plan(self, direction='BULLISH'):
        return bot.TouchPlan(direction, 99 if direction == 'BULLISH' else 101,
                             .01, .7, 100)

    async def walk(self, bars, direction='BULLISH'):
        scanner = object.__new__(bot.TTWScanner)
        return (await scanner.walk_sequence('TESTUSDT', bars, self.plan(direction),
                                             bars[-1].close_time, '1s'))[0]

    async def test_touch_before_bullish_impulse(self):
        bars = [candle(0,100,100,99.4,99.4), candle(1000,99.4,99.4,99,99.05),
                candle(2000,99.05,101,99.05,100.8)]
        event = await self.walk(bars)
        self.assertEqual(event['reason'], 'TOUCH_FIRST')
        self.assertEqual(event['touch_time'], 1000)
        self.assertEqual(event['post_high'], 101)

    async def test_touch_before_bearish_impulse(self):
        bars = [candle(0,100,100,99.4,99.4), candle(1000,99.4,99.4,99,99.05),
                candle(2000,99.05,101,99.05,100.8)]
        event = await self.walk([mirror(c) for c in bars], 'BEARISH')
        self.assertEqual(event['reason'], 'TOUCH_FIRST')

    async def test_bullish_impulse_then_retrace_rejected(self):
        event = await self.walk([candle(0,100,101,100,100.9),
                                 candle(1000,100.9,100.9,99,99.1)])
        self.assertEqual(event['reason'], 'IMPULSE_BEFORE_TOUCH')

    async def test_bearish_impulse_then_retrace_rejected(self):
        bars = [candle(0,100,101,100,100.9), candle(1000,100.9,100.9,99,99.1)]
        self.assertEqual((await self.walk([mirror(c) for c in bars], 'BEARISH'))['reason'],
                         'IMPULSE_BEFORE_TOUCH')

    async def test_small_fluctuations_before_touch_allowed(self):
        bars = [candle(0,100,100.1,99.8,99.9), candle(1000,99.9,100.05,99.6,99.65),
                candle(2000,99.65,99.7,99.3,99.4), candle(3000,99.4,99.45,99,99.05)]
        self.assertEqual((await self.walk(bars))['reason'], 'TOUCH_FIRST')

    async def test_unknown_order_inside_one_second_rejected(self):
        self.assertEqual((await self.walk([candle(0,100,101,99,99.1)]))['reason'],
                         'AMBIGUOUS_ORDER')

    async def test_known_open_touch_precedes_same_second_impulse(self):
        self.assertEqual((await self.walk([candle(0,99,101,99,100.8)]))['reason'],
                         'TOUCH_FIRST')

    async def test_same_parent_ohlc_different_paths(self):
        parent = candle(0,100,101,99,100.8,3600000)
        scanner = object.__new__(bot.TTWScanner)
        # Both paths share the same parent OHLC. Lower-timeframe order decides.
        good = [candle(0,100,100,99,99.1,60000),
                candle(60000,99.1,101,99.1,100.8,60000)]
        bad = [candle(0,100,101,100,100.8,60000),
               candle(60000,100.8,100.8,99,100.8,60000)]
        good_seconds = [candle(0,100,100,99.5,99.5),
                        candle(1000,99.5,99.5,99,99.1)]
        bad_seconds = [candle(0,100,101,100,100.8)]
        async def fetch_good(symbol, interval, start, end):
            return good if interval == '1m' else good_seconds
        async def fetch_bad(symbol, interval, start, end):
            return bad if interval == '1m' else bad_seconds
        scanner.fetch_span = fetch_good
        event, _ = await scanner.walk_sequence('T', [parent], self.plan(),119999)
        self.assertEqual(event['reason'], 'TOUCH_FIRST')
        scanner.fetch_span = fetch_bad
        event, _ = await scanner.walk_sequence('T', [parent], self.plan(),119999)
        self.assertEqual(event['reason'], 'IMPULSE_BEFORE_TOUCH')

    async def test_missing_history_cannot_certify(self):
        scanner = object.__new__(bot.TTWScanner)
        async def get(*a, **kw):
            return [[60000,'100','100','99','99.5','1',119999]]
        scanner._get_json = get
        with self.assertRaises(ValueError):
            await scanner.fetch_span('T','1m',0,119999)

    async def test_pagination_and_prefix_coverage(self):
        scanner = object.__new__(bot.TTWScanner)
        calls = []
        async def get(*a, **kw):
            params=kw['params']; calls.append(params['startTime'])
            start=params['startTime']; end=params['endTime']
            return [[t,'100','100.1','99.9','100','1',t+999]
                    for t in range(start,min(end+1,start+1000000),1000)]
        scanner._get_json=get
        bars=await scanner.fetch_span('T','1s',0,1001999)
        self.assertEqual(len(bars),1002)
        self.assertEqual(calls,[0,1000000])


class ApprovedExamples(unittest.TestCase):
    def examples(self):
        profiles=[([99,99.3,99.6],100.1,101.5),
                  ([99.5,99.5,99.5],99.9,101.4)]
        for tips, op, cl in profiles:
            bars=[candle(0,100.65,101,tips[0],100.15,3600000),
                  candle(3600000,100.2,100.8,tips[1],100.45,3600000),
                  candle(7200000,op,cl+.15,tips[2],cl,3600000)]
            yield 'BULLISH',bars
            yield 'BEARISH',[mirror(c) for c in bars]

    def test_approved_shapes_inside_new_c2_boundary_and_final_closes(self):
        for direction,bars in self.examples():
            with self.subTest(direction=direction,tips=[c.low for c in bars]):
                plan=bot.touch_plan(bars,direction,.05,1,.5)
                self.assertIsNotNone(plan)
                setup=bot.setup_at_touch('TESTUSDT','4H',bars,plan,
                                         {'touch_time':7201000},.1)
                self.assertTrue(bot.final_verdict(bars,asdict(setup),.05).startswith('CONFIRMED'))

    def test_open_too_far_from_touch_rejected(self):
        direction,bars=next(self.examples())
        c=bars[-1]
        bars[-1]=candle(c.open_time,104,105,c.low,105,3600000)
        self.assertIsNone(bot.touch_plan(bars,direction,.05,1,.5))

    def test_wrong_close_invalidates_both_directions(self):
        for direction,bars in self.examples():
            plan=bot.touch_plan(bars,direction,.05,1,.5)
            setup=bot.setup_at_touch('T','4H',bars,plan,{'touch_time':7201000},.1)
            c=bars[-1]
            wrong=c.open-.1 if direction=='BULLISH' else c.open+.1
            bars[-1]=candle(c.open_time,c.open,max(c.high,wrong),min(c.low,wrong),wrong,3600000)
            self.assertIn('did not close',bot.final_verdict(bars,asdict(setup),.05))

    def test_line_is_frozen_and_breach_invalidates(self):
        direction,bars=next(self.examples())
        plan=bot.touch_plan(bars,direction,.05,1,.5)
        setup=bot.setup_at_touch('T','4H',bars,plan,{'touch_time':7201000},.1)
        c=bars[-1]; bars[-1]=candle(c.open_time,c.open,c.high,98,c.close,3600000)
        self.assertEqual(bot.touch_plan(bars,direction,.05,1,.5).level,plan.level)
        self.assertIn('breached',bot.final_verdict(bars,asdict(setup),.05))

    def test_alert_uses_actual_price_and_provisional_label(self):
        direction,bars=next(self.examples())
        plan=bot.touch_plan(bars,direction,.05,1,.5)
        setup=bot.setup_at_touch('TESTUSDT','4H',bars,plan,{'touch_time':7201000},.1)
        text,_=bot.alert_payload(setup,'key')
        self.assertIn('EARLY REJECTION / LIVE',text)
        self.assertIn('Price at alert:',text)
        self.assertLessEqual(len(text+'\n\nCONFIRMED: touch-first sequence and reversal close'),1024)

    def test_old_state_migrates_without_losing_alerts(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            Path(d,'state-v2.json').write_text(json.dumps({'schema_version':2,
                'alerts':{'old':{'status':'sent'}},'telegram_offset':42}))
            store=bot.StateStore(d)
            self.assertIn('old',store.data['alerts'])
            self.assertEqual(store.data['telegram_offset'],42)
            self.assertEqual(store.data['sequence_rejections'],{})


class ScannerFlow(unittest.IsolatedAsyncioTestCase):
    async def test_original_telegram_message_updated_once_at_close(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=SimpleNamespace(telegram_chat_id='private-test',max_concurrency=2,state_dir=d)
            scanner=bot.TTWScanner(cfg)
            direction,bars=next(ApprovedExamples().examples())
            plan=bot.touch_plan(bars,direction,.05,1,.5)
            setup=bot.setup_at_touch('TESTUSDT','4H',bars,plan,{'touch_time':7201000},.1)
            key='TESTUSDT|4H|BULLISH|7200000'
            scanner.store.reserve(key,dict(alert_id=bot.alert_id(key),setup=asdict(setup),
                                          tolerance_pct=.05,delivery_kind='photo'))
            scanner.store.sent(key,123)
            edits=[]
            async def telegram(method,payload):
                edits.append((method,payload)); return {'ok':True}
            scanner.telegram_call=telegram
            await scanner.close_updates('TESTUSDT','4H',bars,10800001)
            await scanner.close_updates('TESTUSDT','4H',bars,10800002)
            self.assertEqual(len(edits),1)
            self.assertEqual(edits[0][0],'editMessageCaption')
            self.assertEqual(edits[0][1]['message_id'],123)
            self.assertIn('CONFIRMED',edits[0][1]['caption'])
            self.assertTrue(scanner.store.data['alerts'][key]['close_update_sent'])

    async def exercise(self, *, late=False, already_moved=False, unavailable=False,
                       premature=False, bearish=False):
        with tempfile.TemporaryDirectory() as d:
            cfg=SimpleNamespace(telegram_chat_id='private-test', max_concurrency=2,
                state_dir=d, wick2_tolerance_pct=.05, max_open_to_touch_pct=1,
                impulse_range_fraction=.5, max_alert_delay=90,
                max_entry_move_fraction=.25, stop_buffer_pct=.1)
            cfg.touch_wick_fraction=.10
            cfg.rejection_wick_fraction=.10
            scanner=bot.TTWScanner(cfg)
            scanner.tick_sizes={'TESTUSDT':.01}
            bars=[candle(0,100.65,101,99,100.15,3600000),
                  candle(3600000,100.2,100.8,99.3,100.45,3600000),
                  candle(7200000,100.1,100.1,99.6,99.75,3600000)]
            if premature:
                bars[-1]=candle(7200000,100.1,101.5,99.6,99.65,3600000)
            seconds=[candle(7200000,100.1,100.1,99.9,99.9),
                     candle(7201000,99.9,99.9,99.6,99.65),
                     candle(7202000,99.65,99.75,99.65,99.75),
                     candle(7203000,99.75,99.75,99.75,99.75)]
            if premature:
                seconds[0]=candle(7200000,100.1,101.5,100.1,101.2)
            if already_moved:
                seconds[-1]=candle(7202000,99.65,101,99.65,99.65)
            if bearish:
                bars=[mirror(c) for c in bars]; seconds=[mirror(c) for c in seconds]
            sent=[]
            async def fetch_bases(symbol): return {}
            async def fetch(symbol, interval, start, end):
                if unavailable: raise ValueError('missing data')
                if interval=='1h': return [bars[-1]]
                if interval=='1m':
                    return [candle(7200000,bars[-1].open,
                        max(c.high for c in seconds),min(c.low for c in seconds),
                        seconds[-1].close,60000)]
                return seconds
            async def send(setup,key,chart):
                sent.append(setup); return 123
            scanner.fetch_symbol_bases=fetch_bases
            scanner.candles_for_timeframe=lambda bases,label:bars
            scanner.fetch_span=fetch
            scanner.send_alert=send
            clock=7300 if late else 7204
            with patch.object(bot,'TIMEFRAMES',{'4H':('4h',1,'native')}), \
                    patch.object(bot.time,'time',return_value=clock):
                await scanner.scan_symbol('TESTUSDT')
                await scanner.scan_symbol('TESTUSDT')
            return sent,scanner.store.data

    async def test_early_touch_alerts_once_while_c3_not_yet_green(self):
        sent,state=await self.exercise()
        self.assertEqual(len(sent),1)
        self.assertLess(sent[0].current_price,sent[0].candle3_open_price)
        self.assertEqual(sent[0].entry,99.75)
        self.assertEqual(len(state['alerts']),1)

    async def test_early_bearish_touch_before_red_close(self):
        sent,_=await self.exercise(bearish=True)
        self.assertEqual(len(sent),1)
        self.assertGreater(sent[0].current_price,sent[0].candle3_open_price)

    async def test_stale_first_touch_not_resurrected(self):
        sent,state=await self.exercise(late=True)
        self.assertEqual(sent,[])
        self.assertTrue(any(v['reason']=='STALE_FIRST_TOUCH'
                            for v in state['sequence_rejections'].values()))

    async def test_touch_then_impulse_then_pullback_not_alerted(self):
        sent,state=await self.exercise(already_moved=True)
        self.assertEqual(sent,[])
        self.assertTrue(any(v['reason']=='IMPULSE_ALREADY_AFTER_TOUCH'
                            for v in state['sequence_rejections'].values()))

    async def test_premature_impulse_never_alerted(self):
        sent,state=await self.exercise(premature=True)
        self.assertEqual(sent,[])
        self.assertTrue(any(v['reason']=='IMPULSE_BEFORE_TOUCH'
                            for v in state['sequence_rejections'].values()))

    async def test_unavailable_history_no_alert_or_permanent_rejection(self):
        sent,state=await self.exercise(unavailable=True)
        self.assertEqual(sent,[])
        self.assertEqual(state['sequence_rejections'],{})


class EarlyRejectionRules(unittest.TestCase):
    def fixture(self, bearish=False):
        bars=[candle(0,100.65,101,99,100.15,3600000),
              candle(3600000,100.2,100.8,99.3,100.45,3600000),
              candle(7200000,100.1,100.1,99.6,99.75,3600000)]
        seconds=[candle(7201000,99.9,99.9,99.6,99.65),
                 candle(7202000,99.65,99.75,99.65,99.75),
                 candle(7203000,99.75,99.75,99.75,99.75)]
        if bearish:
            bars=list(map(mirror,bars)); seconds=list(map(mirror,seconds))
        direction='BEARISH' if bearish else 'BULLISH'
        plan=bot.touch_plan(bars,direction,.05,1,.5,tick_size=.01)
        return bars,seconds,plan

    def ready(self,bars,seconds,plan,now=7204000):
        return bot.rejection_ready(bars,plan,seconds,7201000,now,.25)

    def test_small_live_rejection_works_in_both_directions(self):
        for bearish in (False,True):
            bars,seconds,plan=self.fixture(bearish)
            self.assertTrue(self.ready(bars,seconds,plan))

    def test_touch_without_rejection_does_not_alert(self):
        for bearish in (False,True):
            bars,seconds,plan=self.fixture(bearish)
            c=bars[-1]
            bars[-1]=candle(c.open_time,c.open,c.high,c.low,plan.level,3600000)
            self.assertFalse(self.ready(bars,seconds,plan))

    def test_one_second_rejection_is_not_enough(self):
        bars,seconds,plan=self.fixture()
        self.assertFalse(self.ready(bars,seconds[:2],plan,7203000))

    def test_unfinished_second_cannot_confirm(self):
        bars,seconds,plan=self.fixture()
        self.assertFalse(self.ready(bars,seconds,plan,7203999))

    def test_stale_rejection_cannot_confirm(self):
        bars,seconds,plan=self.fixture()
        self.assertFalse(self.ready(bars,seconds,plan,7210000))

    def test_resumed_push_cancels_current_entry(self):
        bars,seconds,plan=self.fixture()
        c=bars[-1]
        bars[-1]=candle(c.open_time,c.open,c.high,c.low,99.61,3600000)
        self.assertFalse(self.ready(bars,seconds,plan))

    def test_boundary_breach_then_recovery_still_invalid(self):
        for bearish in (False,True):
            bars,seconds,plan=self.fixture(bearish)
            c=bars[-1]
            bars[-1]=candle(c.open_time,c.open,
                bars[1].high+.01 if bearish else c.high,
                c.low if bearish else bars[1].low-.01,c.close,3600000)
            self.assertTrue(bot.boundary_breached(bars,plan.direction))
            self.assertFalse(self.ready(bars,seconds,plan))

    def test_superseded_outward_slopes_require_c2_breach(self):
        bars=[candle(0,100.65,101,99.6,100.15,3600000),
              candle(3600000,100.2,100.8,99.4,100.45,3600000),
              candle(7200000,100,101.25,99.2,101.1,3600000)]
        self.assertIsNone(bot.touch_plan(bars,'BULLISH',.05,1,.5))
        self.assertIsNone(bot.touch_plan(list(map(mirror,bars)),'BEARISH',.05,1,.5))

    def test_tolerance_scales_to_wicks_not_coin_price(self):
        bars,_,plan=self.fixture()
        self.assertAlmostEqual(plan.tolerance,.09)
        self.assertLess(plan.tolerance,2*bars[1].low*.05/100)

    def test_actual_second_wick_residual_is_measured(self):
        bars,_,plan=self.fixture()
        c=bars[-1]
        bars[-1]=candle(c.open_time,c.open,c.high,99.58,c.close,3600000)
        setup=bot.setup_at_touch('TESTUSDT','3H',bars,plan,{'touch_time':7201000},.1)
        self.assertGreater(setup.wick2_deviation_pct,0)

    def test_btc_audit_touch_at_high_is_not_rejection(self):
        # Exact 09:01 UTC snapshot from the deployed bot's audit log.
        bars=[candle(0,84600.01,84725.04,84522.63,84652.09,10800000),
              candle(10800000,84652.09,84674.01,84550,84616.01,10800000),
              candle(21600000,84616.02,84624.04,84616.01,84624.04,10800000)]
        plan=bot.touch_plan(bars,'BEARISH',.05,1,.5,tick_size=.01)
        self.assertIsNotNone(plan)
        self.assertFalse(bot.rejection_ready(bars,plan,[],21629000,21661558,.25))

    def test_eth_audit_requires_breaching_c2_so_rejected(self):
        # Exact 09:03 UTC audit snapshot. Projected touch itself is outside C2.
        bars=[candle(0,2677.15,2682.81,2672.34,2677.74,10800000),
              candle(10800000,2677.74,2684.05,2675.8,2683.01,10800000),
              candle(21600000,2683.01,2686.19,2683.01,2685.48,10800000)]
        self.assertTrue(bot.boundary_breached(bars,'BEARISH'))
        self.assertIsNone(bot.touch_plan(bars,'BEARISH',.05,1,.5,tick_size=.01))

    def test_avax_later_overshoot_cannot_reuse_earlier_touch(self):
        bars=[candle(0,10.864,10.961,10.844,10.888,10800000),
              candle(10800000,10.889,10.945,10.841,10.905,10800000),
              candle(21600000,10.905,10.936,10.903,10.924,10800000)]
        plan=bot.touch_plan(bars,'BEARISH',.05,1,.5,tick_size=.001)
        self.assertIsNotNone(plan)
        self.assertGreater(bars[-1].high-plan.level,plan.tolerance)
        self.assertFalse(bot.rejection_ready(bars,plan,[],21600000,21670000,.25))


class LiveInvalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_breach_marks_original_alert_once(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=SimpleNamespace(telegram_chat_id='private-test',max_concurrency=2,state_dir=d)
            scanner=bot.TTWScanner(cfg)
            bars,_,plan=EarlyRejectionRules().fixture()
            setup=bot.setup_at_touch('TESTUSDT','3H',bars,plan,{'touch_time':7201000},.1)
            key='TESTUSDT|3H|BULLISH|7200000'
            scanner.store.reserve(key,dict(alert_id=bot.alert_id(key),setup=asdict(setup),delivery_kind='photo'))
            scanner.store.sent(key,123)
            c=bars[-1]; bars[-1]=candle(c.open_time,c.open,c.high,99.2,c.close,3600000)
            edits=[]
            async def telegram(method,payload):
                edits.append((method,payload)); return {'ok':True}
            scanner.telegram_call=telegram
            await scanner.live_invalidations('TESTUSDT','3H',bars,7205000)
            await scanner.live_invalidations('TESTUSDT','3H',bars,7206000)
            self.assertEqual(len(edits),1)
            self.assertEqual(edits[0][1]['message_id'],123)
            self.assertIn('INVALIDATED LIVE',edits[0][1]['caption'])
            self.assertIn('hard C2 wick boundary',edits[0][1]['caption'])


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
