import asyncio
from dataclasses import replace
import json
import math
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ttw_v3 as bot


def candle(t, o, h, l, c, duration=3600000):
    return bot.Candle(t, o, h, l, c, 1, t+duration-1)


def fixture():
    # Illustrative shapes, not alleged exact OHLC from the user's screenshots.
    return [candle(0, 102, 103, 100, 101),
            candle(3600000, 101, 102, 99, 100.8),
            candle(7200000, 100, 100, 98.01, 98.31)]


def approach():
    return [candle(-10800000, 106, 107, 104, 105),
            candle(-7200000, 105, 106, 103, 104),
            candle(-3600000, 104, 105, 101, 102)]


class ReversalContext(unittest.TestCase):
    def test_opposing_approach_both_c1_colours_and_directions(self):
        for bear in (False, True):
            for reverse in (False, True):
                bars = approach()+fixture()
                if reverse:
                    bars[3] = replace(bars[3], open=101, close=102)
                if bear: bars = list(map(reciprocal, bars))
                self.assertTrue(bot.reversal_context(bars, 'BEARISH' if bear else 'BULLISH', .001)['ok'])

    def test_reversal_colour_after_same_colour_fails_even_with_downward_approach(self):
        bars=approach()+fixture()
        bars[2]=replace(bars[2], open=101.5, close=102)
        bars[3]=replace(bars[3], open=101, close=102)
        for bear in (False, True):
            test=list(map(reciprocal,bars)) if bear else bars
            self.assertEqual(bot.reversal_context(test,'BEARISH' if bear else 'BULLISH',.001)['reason'],
                             'C1_REVERSAL_COLOUR_ALREADY_STARTED')

    def test_mixed_approach_colours_allowed(self):
        bars=approach()+fixture()
        bars[1]=replace(bars[1],open=103.5,close=104)
        self.assertTrue(bot.reversal_context(bars,'BULLISH',.001)['ok'])

    def test_continuation_and_flat_approaches_rejected(self):
        rising=list(map(reciprocal,approach()))+fixture()
        self.assertEqual(bot.reversal_context(rising,'BULLISH',.001)['reason'],'NO_OPPOSING_APPROACH')
        flat=[candle(t,104,105,103,104) for t in (-10800000,-7200000,-3600000)]+fixture()
        self.assertFalse(bot.reversal_context(flat,'BULLISH',.001)['ok'])

    def test_missing_or_gapped_context_rejected(self):
        self.assertFalse(bot.reversal_context(fixture(),'BULLISH',.001)['ok'])
        bars=approach()+fixture();bars[0]=replace(bars[0],close_time=-7200002)
        self.assertFalse(bot.reversal_context(bars,'BULLISH',.001)['ok'])

    def test_later_move_cannot_manufacture_approach(self):
        bars=approach()+fixture();before=bot.reversal_context(bars,'BULLISH',.001)
        bars[-1]=replace(bars[-1],high=120,close=119)
        self.assertEqual(bot.reversal_context(bars,'BULLISH',.001),before)


def reciprocal(b):
    return replace(b, open=10000/b.open, high=10000/b.low,
                   low=10000/b.high, close=10000/b.close)


def live_watch(bear=False):
    bars = approach() + fixture()
    bars[-1] = replace(bars[-1], open=98.28)
    if bear: bars = list(map(reciprocal, bars))
    direction = 'BEARISH' if bear else 'BULLISH'
    plan = bot.make_plan(bars[-3:], direction, .001)
    w = bot.Watch('TESTUSDT|4H|'+direction+'|7200000', 'TESTUSDT', '4H', bars, plan)
    w.verified_ms = 7200000
    values = [99.5, 98.01, 98.31, 98.31]
    for t, value in zip((7200100, 7201000, 7202000, 7204000), values):
        w.observe(10000/value if bear else value, t)
    return w


class LogGeometry(unittest.TestCase):
    def test_log_midpoint_and_projection(self):
        self.assertAlmostEqual(bot.line_price(100, 121, 1), 110)
        self.assertNotAlmostEqual(bot.line_price(100, 121, 1), 110.5)
        self.assertAlmostEqual(bot.make_plan(fixture(), 'BULLISH', .001).level, 98.01)

    def test_outward_slopes_and_both_colours_accepted(self):
        for bear in (False, True):
            bars = fixture()
            if bear: bars = list(map(reciprocal, bars))
            side = 'BEARISH' if bear else 'BULLISH'
            self.assertIsNotNone(bot.geometry(bars, side, .001))
            c = bars[0]
            bars[0] = replace(c, open=c.close, close=c.open)
            self.assertIsNotNone(bot.make_plan(bars, side, .001))
            self.assertIsNotNone(bot.geometry(bars, side, .001))

    def test_c2_near_miss_and_slight_breach_are_symmetric(self):
        for delta in (-.1, .1):
            bars = fixture(); bars[1] = replace(bars[1], low=99+delta)
            result = bot.geometry(bars, 'BULLISH', .001)
            self.assertIsNotNone(result)
            self.assertEqual(result['c2_contact'], 'SLIGHT_BREACH' if delta < 0 else 'NEAR_MISS')

    def test_deep_wick_penetration_rejected(self):
        bars = fixture(); bars[1] = replace(bars[1], low=98)
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001))

    def test_near_style_tiny_live_wick_does_not_force_entry(self):
        bars = [candle(0,4.845,4.991,4.590,4.694),
                candle(3600000,4.694,4.739,4.621,4.670),
                candle(7200000,4.670,4.670,4.648,4.657)]
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001))

    def test_c3_shortness_does_not_shrink_c2_allowance(self):
        bars = fixture(); bars[1] = replace(bars[1], low=99.1)
        self.assertIsNotNone(bot.geometry(bars, 'BULLISH', .001))
        bars[2] = replace(bars[2], close=98.28)
        self.assertIsNotNone(bot.geometry(bars, 'BULLISH', .001))

    def test_body_intersection_never_repaired_by_tolerance(self):
        bars = fixture(); bars[1] = replace(bars[1], close=99)
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001, .3, 2))

    def test_missing_wick_and_invalid_ohlc_rejected(self):
        bars = fixture(); bars[2] = replace(bars[2], close=bars[2].low)
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001))
        bars[2] = replace(bars[2], high=bars[2].low-.1)
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001))

    def test_percentage_scale_invariance(self):
        bars = fixture(); first = bot.geometry(bars, 'BULLISH', .001)
        scaled = [replace(b, open=b.open*100, high=b.high*100, low=b.low*100, close=b.close*100) for b in bars]
        second = bot.geometry(scaled, 'BULLISH', .1)
        self.assertAlmostEqual(first['wick_fraction'], second['wick_fraction'])

    def test_reported_btc_at_high_still_rejected_without_c2_boundary(self):
        bars=[candle(0,84600.01,84725.04,84522.63,84652.09,10800000),
              candle(10800000,84652.09,84674.01,84550,84616.01,10800000),
              candle(21600000,84616.02,84624.04,84616.01,84624.04,10800000)]
        self.assertIsNone(bot.geometry(bars,'BEARISH',.01))

    def test_reported_eth_bad_alignment_not_accepted_by_allowing_c2_cross(self):
        bars=[candle(0,2677.15,2682.81,2672.34,2677.74,10800000),
              candle(10800000,2677.74,2684.05,2675.8,2683.01,10800000),
              candle(21600000,2683.01,2686.19,2683.01,2685.48,10800000)]
        self.assertIsNone(bot.geometry(bars,'BEARISH',.01))


class LiveSequence(unittest.TestCase):
    def test_observed_rejection_accepts_both_directions(self):
        for bear in (False, True):
            w = live_watch(bear)
            self.assertTrue(w.ready(7204000))

    def test_colour_flip_needs_no_hold_but_verification_and_fresh_price(self):
        w = live_watch()
        self.assertTrue(w.ready(7204000))
        self.assertFalse(w.ready(7210000))
        w.verified_ms = 0
        self.assertFalse(w.ready(7204000))

    def test_retap_waits_for_colour_flip_without_new_hold(self):
        w = live_watch(); w.observe(98.05, 7204500)
        self.assertFalse(w.ready(7204600))
        w.observe(98.31, 7205000)
        self.assertTrue(w.ready(7205000))
        w.observe(98.31, 7207000)
        self.assertTrue(w.ready(7207000))

    def test_wrong_colour_and_doji_reject_both_directions(self):
        for bear in (False, True):
            for price in (98.279, 98.28):
                w = live_watch(bear)
                w.observe(10000/price if bear else price, 7205000)
                self.assertFalse(w.ready(7205000))
                w.observe(10000/98.31 if bear else 98.31, 7205100)
                self.assertTrue(w.ready(7205100))

    def test_recoil_alone_cannot_alert_before_colour_flip(self):
        for bear in (False, True):
            w = live_watch(bear)
            w.plan = replace(w.plan, candle3_open=10000/100 if bear else 100)
            self.assertFalse(w.ready(7204000))

    def test_4d_is_scanned_and_has_tradingview_interval(self):
        self.assertEqual(bot.TIMEFRAMES['4D'], ('1d', 4, 'day'))
        self.assertEqual(bot.infra.TV_INTERVAL['4D'], '4D')

    def test_impulse_before_touch_is_permanent(self):
        w = live_watch(); w = bot.Watch(w.key,w.symbol,w.timeframe,fixture(),w.plan)
        w.verified_ms=7200000
        w.observe(101.8,7200100); w.observe(98.01,7201000); w.observe(98.31,7205000)
        self.assertEqual(w.failed, 'IMPULSE_BEFORE_TOUCH')
        self.assertFalse(w.ready(7205000))

    def test_impulse_then_return_not_fresh_signal(self):
        w = live_watch(); w.observe(100,7205000); w.observe(98.31,7206000)
        self.assertEqual(w.failed, 'IMPULSE_ALREADY_AFTER_TOUCH')
        self.assertFalse(w.ready(7206000))

    def test_old_first_touch_and_zone_overshoot_rejected(self):
        w=live_watch(); w.observe(98.31,7300000)
        self.assertFalse(w.ready(7300000)); self.assertEqual(w.failed,'STALE_FIRST_TOUCH')
        w=live_watch(); w.observe(w.plan.lower-.01,7205000)
        self.assertEqual(w.failed,'GEOMETRY_ZONE_EXCEEDED')

    def test_after_alert_price_tracking_continues_beyond_candidate_zone(self):
        w=live_watch(); w.alerted=True
        w.observe(97,7205000); w.observe(96,7206000)
        self.assertEqual(w.last_price,96)
        self.assertEqual(w.bars[-1].low,96)


class AlertContract(unittest.TestCase):
    def test_stops_protective_and_ordered_for_outward_slopes(self):
        for bear in (False, True):
            s=bot.make_setup(live_watch(bear),.1)
            self.assertTrue(s['entry'] < s['sl1'] < s['sl2'] if bear
                            else s['sl2'] < s['sl1'] < s['entry'])

    def test_tp_direction_and_4h_boundary(self):
        for side,sign in [('BULLISH',1),('BEARISH',-1)]:
            for tf,a,b in [('2H',3,5),('4H',3,5),('6H',5,10),('1M',5,10)]:
                t1,t2,p1,p2=bot.targets(100,side,tf)
                self.assertEqual((p1,p2),(a,b))
                self.assertAlmostEqual(t1,100+sign*a); self.assertAlmostEqual(t2,100+sign*b)

    def test_concise_caption_has_exact_fields_and_chart_button(self):
        s=bot.make_setup(live_watch(),.1); text,buttons=bot.alert_payload(s,'key')
        self.assertEqual(len(text.splitlines()),6)
        for label in ('TEST/USDT · 4H','Price at alert:','Entry:','SL1:','SL2:','TP:'):
            self.assertIn(label,text)
        self.assertNotIn('boundary',text); self.assertLess(len(text),1024)
        self.assertIn('interval=240',buttons['inline_keyboard'][1][0]['url'])

    def test_stop_percentages_measured_from_entry_both_sides(self):
        for bear in (False,True):
            setup=bot.make_setup(live_watch(bear),.1)
            text,_=bot.alert_payload(setup,'key')
            for name in ('sl1','sl2'):
                distance=abs(setup[name]/setup['entry']-1)*100
                self.assertIn(f"{name.upper()}: {bot.infra.fmt_price(setup[name])} ({distance:.2f}%)",text)

    def test_chart_is_logarithmic_and_png(self):
        import matplotlib.axes
        calls=[]; original=matplotlib.axes.Axes.set_yscale
        def record(ax,scale,*args,**kwargs):
            calls.append(scale); return original(ax,scale,*args,**kwargs)
        w=live_watch(); s=bot.make_setup(w,.1)
        with patch.object(matplotlib.axes.Axes,'set_yscale',record):
            png=bot.render_chart(w.bars,s)
        self.assertIn('log',calls); self.assertTrue(png.startswith(b'\x89PNG'))


class ScannerIntegration(unittest.IsolatedAsyncioTestCase):
    def scanner(self, folder):
        return bot.Scanner(SimpleNamespace(telegram_chat_id='private-test',max_concurrency=2,
            state_dir=folder,stop_buffer_pct=.1,send_charts=False,rejection_hold_seconds=2,
            max_alert_delay=90,max_entry_move_fraction=.25))

    async def test_history_covers_context_for_every_aggregated_timeframe(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d);requests=[]
            async def get(url,params):
                requests.append(params)
                interval=params['interval']
                durations={'1h':3600000,'2h':7200000,'4h':14400000,'6h':21600000,
                           '12h':43200000,'1d':86400000,'3d':259200000,'1w':604800000}
                if interval=='1M':
                    from datetime import datetime,timezone
                    starts=[int(datetime(2023+i//12,1+i%12,1,tzinfo=timezone.utc).timestamp()*1000)
                            for i in range(params['limit']+1)]
                else:
                    duration=durations[interval]
                    offset=345600000 if interval=='1w' else 0
                    starts=[offset+i*duration for i in range(params['limit']+1)]
                return [[a,100,102,99,101,1,b-1] for a,b in zip(starts,starts[1:])]
            scanner._get_json=get
            bases=await scanner.fetch_symbol_bases('TESTUSDT')
            for tf in bot.TIMEFRAMES:
                self.assertGreaterEqual(len(scanner.candles_for_timeframe(bases,tf)),6,tf)
            self.assertTrue(all(p['limit']<=1000 for p in requests))

    async def test_delivery_snapshot_and_duplicate_suppression(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); w=live_watch(); scanner.stream_connected=True
            calls=[]
            async def telegram(method,payload):
                calls.append((method,payload)); return {'ok':True,'result':{'message_id':123}}
            scanner.telegram_call=telegram
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            self.assertTrue(w.alerted); self.assertFalse(w.ready(7204000))
            self.assertEqual(len(calls),1)
            self.assertEqual(scanner.store.data['alerts'][w.key]['status'],'sent')
            saved=scanner.store.data['alerts'][w.key]['setup']
            self.assertEqual(saved['entry'],98.31)
            self.assertEqual(saved['price_scale'] if 'price_scale' in saved else
                             scanner.store.data['alerts'][w.key]['price_scale'],'LOG')

    async def test_context_guard_prevents_continuation_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d);scanner.stream_connected=True;w=live_watch()
            w.bars=list(map(reciprocal,approach()))+w.bars[-3:]
            await scanner.send_watch(w)
            self.assertEqual(scanner.store.data['alerts'],{})
            self.assertEqual(scanner.store.data['sequence_rejections'][w.key]['reason'],'NO_OPPOSING_APPROACH')

    async def test_disconnected_stream_cannot_send(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); w=live_watch()
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            self.assertEqual(scanner.store.data['alerts'],{})

    async def test_uncertain_delivery_is_reserved_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); scanner.stream_connected=True; w=live_watch()
            async def fail(*a,**k): raise TimeoutError()
            scanner.telegram_call=fail
            with patch.object(bot.time,'time',return_value=7204): await scanner.send_watch(w)
            self.assertEqual(scanner.store.data['alerts'][w.key]['status'],'pending')
            self.assertTrue(w.alerted)

    async def test_stream_gap_discards_unverified_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); scanner.symbols=['TESTUSDT']; w=live_watch()
            scanner.watches[w.key]=w
            scanner.accept_trade('TESTUSDT',10,7205000,98.31)
            scanner.accept_trade('TESTUSDT',12,7206000,98.31)
            self.assertNotIn(w.key,scanner.watches)
            self.assertEqual(scanner.stats['stream_gaps'],1)

    async def test_immutable_stop_invalidation_once_and_compact(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); w=live_watch(); s=bot.make_setup(w,.1)
            s['strategy_version']='3.0'
            scanner.store.reserve(w.key,dict(alert_id=bot.infra.alert_id(w.key),setup=s,delivery_kind='photo'))
            scanner.store.sent(w.key,123); w.alerted=True; scanner.watches[w.key]=w
            calls=[]
            async def telegram(method,payload): calls.append(payload); return {'ok':True}
            scanner.telegram_call=telegram
            w.observe(s['sl1']-.01,7205000)
            await scanner.update_live_records(7205000); await scanner.update_live_records(7206000)
            self.assertEqual(len(calls),1); self.assertIn('INVALID',calls[0]['caption'])
            self.assertEqual(scanner.store.data['alerts'][w.key]['setup']['sl1'],s['sl1'])

    async def test_closed_c3_reversal_edited_once(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); w=live_watch(); s=bot.make_setup(w,.1)
            s['strategy_version']='3.0'
            scanner.store.reserve(w.key,dict(alert_id=bot.infra.alert_id(w.key),setup=s,delivery_kind='photo'))
            scanner.store.sent(w.key,123); bars=fixture()
            bars[-1]=replace(bars[-1],close=100.2,high=100.2)
            calls=[]
            async def telegram(method,payload): calls.append(payload); return {'ok':True}
            scanner.telegram_call=telegram
            await scanner.close_updates('TESTUSDT','4H',bars,10800000)
            await scanner.close_updates('TESTUSDT','4H',bars,10800001)
            self.assertEqual(len(calls),1); self.assertIn('CONFIRMED',calls[0]['caption'])

    async def test_verification_refines_partial_ohlc_and_replays_buffer(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=self.scanner(d); scanner.stream_connected=True
            w=live_watch(); w=bot.Watch(w.key,w.symbol,w.timeframe,fixture(),replace(w.plan,candle3_open=100))
            scanner.buffers[w.symbol]=[(7204000,98.31),(7206000,98.31)]
            calls=[]
            async def fetch(symbol,interval,start,end):
                calls.append(interval)
                if interval=='1h': return [candle(7200000,100,101,98.01,100)]
                if interval=='1m': return [candle(7200000,100,101,98.01,100,60000)]
                return [candle(7200000,100,100,99.5,99.5,1000),
                        candle(7201000,99.5,99.5,98.01,98.02,1000),
                        candle(7202000,98.02,98.31,98.02,98.31,1000),
                        candle(7203000,98.31,98.31,98.31,98.31,1000)]
            async def walk(*args,**kwargs):
                return dict(reason='TOUCH_FIRST',touch_time=7201000,post_high=98.31,post_low=98.01),98.01
            scanner.fetch_span=fetch; scanner.walk_sequence=walk
            with patch.object(bot.time,'time',return_value=7204): await scanner.verify_watch(w)
            self.assertEqual(calls,['1h','1m','1s'])
            self.assertEqual(w.bars[-1].high,100)
            self.assertFalse(w.ready(7206000))


class WebSocketFrames(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_preserves_case_sensitive_accept_value(self):
        key=bot.base64.b64encode(b'0123456789abcdef').decode()
        accept=bot.base64.b64encode(bot.hashlib.sha1(
            (key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.assertNotEqual(accept,accept.lower())
        reader=asyncio.StreamReader()
        reader.feed_data(('HTTP/1.1 101 Switching Protocols\r\n'
                          'Upgrade: websocket\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode())
        class Writer:
            def write(self,data): self.request=data
            async def drain(self): pass
        writer=Writer(); ws=bot.WebSocket()
        async def connect(*args,**kwargs): return reader,writer
        with patch.object(bot.asyncio,'open_connection',side_effect=connect), \
                patch.object(bot.secrets,'token_bytes',return_value=b'0123456789abcdef'):
            await ws.connect('wss://stream.binance.com:443/stream?streams=btcusdt@aggTrade')
        self.assertIn(key.encode(),writer.request)

    async def test_fragmented_json_and_ping(self):
        ws=bot.WebSocket(); ws.reader=asyncio.StreamReader(); calls=[]
        async def send(opcode,data): calls.append((opcode,data))
        ws.send=send
        a=b'{"data":'; b=b'{"e":"aggTrade"}}'
        ws.reader.feed_data(bytes([1,len(a)])+a + bytes([0x89,1])+b'x' + bytes([0x80,len(b)])+b)
        ws.reader.feed_data(b'\x88\x00')
        found=[m async for m in ws.messages()]
        self.assertEqual(found,[{'data':{'e':'aggTrade'}}]); self.assertEqual(calls,[(10,b'x')])

    async def test_oversized_frame_rejected(self):
        ws=bot.WebSocket(); ws.reader=asyncio.StreamReader()
        ws.reader.feed_data(b'\x81\x7f'+(3_000_000).to_bytes(8,'big'))
        with self.assertRaises(ValueError):
            await anext(ws.messages())


if __name__ == '__main__': unittest.main()
