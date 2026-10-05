import asyncio
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

import TTW_BOT_V2 as infra
from candle_feed import CandleFeed
from market_io import MarketCooldown
import test_ttw_v3 as fixtures


def event(bar, at, closed=False, interval='1h'):
    return dict(s='TESTUSDT', E=at, k=dict(i=interval, t=bar.open_time,
        T=bar.close_time, o=str(bar.open), h=str(bar.high), l=str(bar.low),
        c=str(bar.close), v=str(bar.volume), x=closed))


class NativeCandles(unittest.TestCase):
    def setUp(self):
        self.clock = 10
        self.feed = CandleFeed(lambda: self.clock)
        self.bars = fixtures.fixture()
        self.now = self.bars[-1].open_time+5000

    def seed(self):
        self.feed.seed('TESTUSDT', '1h', self.bars, self.now)
        self.feed.accept(event(self.bars[-1], self.now))

    def test_seeded_stream_updates_without_mutating_completed_anchors(self):
        self.seed()
        tail = replace(self.bars[-1], high=101, low=97, close=99)
        self.feed.accept(event(tail, self.now+1000))
        result = self.feed.get('TESTUSDT','1h',self.now+1000)
        self.assertEqual(result[:-1],self.bars[:-1])
        self.assertEqual(result[-1],tail)
        result.pop()
        self.assertEqual(len(self.feed.get('TESTUSDT','1h',self.now+1000)),3)

    def test_rollover_needs_final_event_and_contiguous_next_open(self):
        self.seed(); tail=self.bars[-1]
        next_bar=replace(tail,open_time=tail.close_time+1,close_time=tail.close_time+3600000)
        self.feed.accept(event(tail,tail.close_time,True))
        self.feed.accept(event(next_bar,next_bar.open_time+100))
        result=self.feed.get('TESTUSDT','1h',next_bar.open_time+100)
        self.assertEqual(result, self.bars[1:]+[next_bar])

    def test_missing_final_event_or_skipped_interval_forces_reseed(self):
        for skipped in (False,True):
            self.setUp();self.seed();tail=self.bars[-1]
            if skipped:self.feed.accept(event(tail,tail.close_time,True))
            shift=7200000 if skipped else 3600000
            next_bar=replace(tail,open_time=tail.open_time+shift,close_time=tail.close_time+shift)
            self.feed.accept(event(next_bar,next_bar.open_time+100))
            self.assertIsNone(self.feed.get('TESTUSDT','1h',next_bar.open_time+100))

    def test_stale_disconnected_or_delayed_messages_never_supply_scan(self):
        self.seed();self.clock+=11
        self.assertIsNone(self.feed.get('TESTUSDT','1h',self.now+11000))
        self.clock=10
        self.assertIsNone(self.feed.get('TESTUSDT','1h',self.now+11000))
        self.feed.clear('TESTUSDT')
        self.assertIsNone(self.feed.get('TESTUSDT','1h',self.now))

    def test_event_before_rest_completion_cannot_regress_seed_close(self):
        old=replace(self.bars[-1],close=98.05)
        self.feed.accept(event(old,self.now-1000))
        self.feed.seed('TESTUSDT','1h',self.bars,self.now)
        self.assertEqual(self.feed.get('TESTUSDT','1h',self.now)[-1].close,self.bars[-1].close)
        self.feed.accept(event(old,self.now-2000))
        self.assertEqual(self.feed.get('TESTUSDT','1h',self.now)[-1].close,self.bars[-1].close)

    def test_monthly_rollover_uses_exchange_bounds(self):
        # February into March, without a guessed fixed month duration.
        from datetime import datetime,timezone
        stamps=[int(datetime(2026,m,1,tzinfo=timezone.utc).timestamp()*1000) for m in (1,2,3,4)]
        bars=[fixtures.candle(a,100,101,99,100,b-a) for a,b in zip(stamps,stamps[1:])]
        self.feed.seed('TESTUSDT','1M',bars[:2],stamps[1]+1000)
        self.feed.accept(event(bars[1],stamps[2]-1,True,'1M'))
        self.feed.accept(event(bars[2],stamps[2]+1000,False,'1M'))
        self.assertEqual(self.feed.get('TESTUSDT','1M',stamps[2]+1000),bars[1:])

    def test_bad_candle_or_changed_open_cannot_supply_scan(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.feed.accept(event(replace(self.bars[-1],low=-1),self.now+1))
        self.feed.accept(event(replace(self.bars[-1],open=99),self.now+2))
        self.assertIsNone(self.feed.get('TESTUSDT','1h',self.now+2))


class ScannerReliability(unittest.IsolatedAsyncioTestCase):
    def scanner(self,d): return fixtures.ScannerIntegration().scanner(d)

    async def test_scanner_reuses_stream_candles_during_soft_rest_pause(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);s.stream_connected=True
            bars=fixtures.fixture();now=bars[-1].open_time+5000
            s.candle_feed.seed('TESTUSDT','1h',bars,now)
            s.candle_feed.accept(event(bars[-1],now))
            s.market_io.state.update(until=s.market_io.wall()+62,code='USAGE_HEADROOM')
            async def no_request(*a,**k):self.fail('Streaming scan made a REST request')
            s._get_json=no_request
            with patch('ttw_v3.time.time',return_value=now/1000):
                self.assertEqual(await s.fetch_klines('TESTUSDT','1h'),bars)

    async def test_soft_pause_preserves_candidate_and_allows_verified_fresh_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);w=fixtures.live_watch();s.stream_connected=True
            s.watches[w.key]=w
            s.market_io.state.update(until=s.market_io.wall()+62,code='USAGE_HEADROOM')
            with self.assertRaises(MarketCooldown):await s._get_json(infra.BINANCE_API+'/api/v3/time')
            self.assertIs(s.watches[w.key],w);self.assertFalse(s.market_recovering)
            calls=[]
            async def telegram(method,payload):calls.append(method);return {'result':{'message_id':1}}
            s.telegram_call=telegram
            with patch('ttw_v3.time.time',return_value=w.last_ms/1000):await s.send_watch(w)
            self.assertIn('sendMessage',calls)

    async def test_unverified_candidate_cannot_deliver_during_soft_pause(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);w=fixtures.live_watch();w.verified_ms=0;s.stream_connected=True
            s.market_io.state.update(until=s.market_io.wall()+62,code='USAGE_HEADROOM')
            with patch('ttw_v3.time.time',return_value=w.last_ms/1000):await s.send_watch(w)
            self.assertEqual(s.store.data['alerts'],{})

    async def test_stale_or_wrong_generation_stream_cannot_deliver_during_soft_pause(self):
        for stale in (False,True):
            with tempfile.TemporaryDirectory() as d:
                s=self.scanner(d);w=fixtures.live_watch();s.stream_connected=True
                s.market_io.state.update(until=s.market_io.wall()+62,code='USAGE_HEADROOM')
                if not stale:s.stream_epoch+=1
                with patch('ttw_v3.time.time',return_value=w.last_ms/1000+(4 if stale else 0)):
                    await s.send_watch(w)
                self.assertEqual(s.store.data['alerts'],{})

    async def test_trade_gap_discards_stream_candle_seed(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);s.symbols=['TESTUSDT'];bars=fixtures.fixture();now=7205000
            s.candle_feed.seed('TESTUSDT','1h',bars,now);s.candle_feed.accept(event(bars[-1],now))
            s.accept_trade('TESTUSDT',10,now,98.31)
            s.accept_trade('TESTUSDT',12,now+1,98.31)
            self.assertIsNone(s.candle_feed.get('TESTUSDT','1h',now+1))

    async def test_deferred_candidate_retained_for_stream_retry(self):
        import ttw_v3 as bot
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);w=fixtures.live_watch();s.stream_connected=True
            s.tick_sizes={'TESTUSDT':.001}
            s.cfg.geometry_wick_fraction=.2;s.cfg.geometry_price_cap_pct=1
            s.cfg.impulse_range_fraction=.5;s.cfg.rejection_wick_fraction=.1
            s.cfg.max_open_gap_range=1.5
            async def bases(symbol):return w.bars
            async def defer(watch):raise MarketCooldown('REST headroom')
            s.fetch_symbol_bases=bases;s.candles_for_timeframe=lambda bases,tf:bases
            s.verify_watch=defer
            with patch.object(bot,'TIMEFRAMES',{'4H':('4h',1,'native')}),patch('ttw_v3.time.time',return_value=7204):
                await s.scan_symbol('TESTUSDT')
                count=s.stats['verification_retry']
                await s.scan_symbol('TESTUSDT')
            self.assertEqual(s.stats['verification_retry'],count)
            self.assertTrue(s.watches)
            retained=next(iter(s.watches.values()))
            self.assertEqual(retained.verified_ms,0)
            self.assertFalse(retained.busy)
            self.assertIn(retained.key,s.verify_retry_at)

    async def test_all_native_intervals_scan_without_rest_after_stream_seed(self):
        import ttw_v3 as bot
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);s.stream_connected=True
            now=int(datetime(2026,10,5,8,30,tzinfo=timezone.utc).timestamp()*1000)
            intervals={v[0] for v in bot.TIMEFRAMES.values()}
            lengths={'1h':3600000,'2h':7200000,'4h':14400000,'6h':21600000,
                     '12h':43200000,'1d':86400000,'3d':259200000,'1w':604800000}
            for i in intervals:
                if i=='1M':
                    starts=[int(datetime(2026,m,1,tzinfo=timezone.utc).timestamp()*1000) for m in range(3,12)]
                else:
                    size=lengths[i];offset=345600000 if i=='1w' else 0
                    start=(now-offset)//size*size+offset
                    starts=[start+j*size for j in range(-7,2)]
                bars=[fixtures.candle(a,100,102,99,101,b-a) for a,b in zip(starts,starts[1:])]
                s.candle_feed.seed('TESTUSDT',i,bars,now)
                s.candle_feed.accept(event(bars[-1],now,interval=i))
            async def no_request(*a,**k):self.fail('Native setup scan requested REST')
            s._get_json=no_request
            with patch('ttw_v3.time.time',return_value=now/1000):
                for _ in range(5):
                    bases=await s.fetch_symbol_bases('TESTUSDT')
                    self.assertEqual(set(bases),intervals)
            self.assertEqual(s.stats['stream_candle_hits'],45)


if __name__ == '__main__':unittest.main()
