import asyncio
from dataclasses import replace
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from market_io import MarketIO, MarketCooldown
import TTW_BOT_V2 as infra
import test_ttw_v3 as fixtures


class Clock:
    def __init__(self): self.now=1000000.; self.delays=[]
    def wall(self): return self.now
    async def sleep(self,delay): self.delays.append(delay);self.now+=delay


def error(code,header=None,body=b'{}'):
    return urllib.error.HTTPError('https://api.binance.com/api/v3/time',code,'rate limit',
                                  {'Retry-After':header} if header else {},BytesIO(body))


class MarketSafety(unittest.IsolatedAsyncioTestCase):
    async def test_429_pauses_every_queued_request_and_survives_new_instance(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();calls=[]
            def request(*args): calls.append(args);raise error(429,'120')
            io=MarketIO(infra.StateStore(d),request,clock.wall,clock.wall,clock.sleep)
            results=await asyncio.gather(*(io.get('https://api.binance.com/api/v3/time') for _ in range(5)),return_exceptions=True)
            self.assertTrue(all(isinstance(r,MarketCooldown) for r in results))
            self.assertEqual(len(calls),1)
            restored=MarketIO(infra.StateStore(d),request,clock.wall,clock.wall,clock.sleep)
            self.assertTrue(restored.cooling)
            with self.assertRaises(MarketCooldown): await restored.get('https://api.binance.com/api/v3/klines',{'interval':'1h'})
            self.assertEqual(len(calls),1)

    async def test_418_obeys_retry_time_and_resumes_only_after_margin(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();calls=[]
            def request(*args):
                calls.append(args)
                if len(calls)==1: raise error(418,'600')
                return {'serverTime':1},{}
            io=MarketIO(infra.StateStore(d),request,clock.wall,clock.wall,clock.sleep)
            with self.assertRaises(MarketCooldown): await io.get('https://api.binance.com/api/v3/time')
            clock.now+=601
            with self.assertRaises(MarketCooldown): await io.get('https://api.binance.com/api/v3/time')
            clock.now+=2
            self.assertEqual(await io.get('https://api.binance.com/api/v3/time'),{'serverTime':1})
            self.assertEqual(len(calls),2)

    async def test_missing_retry_header_uses_exponential_cooldown(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();io=MarketIO(infra.StateStore(d),lambda *a:None,clock.wall,clock.wall,clock.sleep)
            io.block(error(418));first=io.state['until']-clock.now
            clock.now=io.state['until']+1
            io.block(error(418));self.assertGreater(io.state['until']-clock.now,first)

    async def test_absolute_expiry_body_does_not_shorten_header(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();io=MarketIO(infra.StateStore(d),lambda *a:None,clock.wall,clock.wall,clock.sleep)
            io.block(error(418,'120',b'{"retryAfter":1000500000}'))
            self.assertEqual(io.state['until'],clock.now+502)

    async def test_shared_ip_usage_causes_headroom_pause(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();io=MarketIO(infra.StateStore(d),lambda *a:({}, {'X-MBX-USED-WEIGHT-1M':'4000'}),clock.wall,clock.wall,clock.sleep)
            await io.get('https://api.binance.com/api/v3/time')
            self.assertTrue(io.cooling)
            with self.assertRaises(MarketCooldown): await io.get('https://api.binance.com/api/v3/exchangeInfo')
            self.assertEqual(io.stats['requests'],1)

    async def test_latest_candle_cache_refreshes_at_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();clock.now=7199;calls=[]
            def request(*a): calls.append(a);return [[len(calls)]],{}
            io=MarketIO(infra.StateStore(d),request,clock.wall,clock.wall,clock.sleep)
            p={'symbol':'TESTUSDT','interval':'2h','limit':8}
            first=await io.get('https://api.binance.com/api/v3/klines',p)
            self.assertEqual(await io.get('https://api.binance.com/api/v3/klines',p),first)
            clock.now=7200
            self.assertNotEqual(await io.get('https://api.binance.com/api/v3/klines',p),first)
            self.assertEqual(len(calls),2)

    async def test_recovery_requests_never_use_mutable_response_cache(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();calls=[]
            def request(*a): calls.append(a);return [],{}
            io=MarketIO(infra.StateStore(d),request,clock.wall,clock.wall,clock.sleep)
            p={'interval':'1s','startTime':0,'endTime':999,'symbol':'TESTUSDT'}
            await io.get('https://api.binance.com/api/v3/klines',p)
            await io.get('https://api.binance.com/api/v3/klines',p)
            self.assertEqual(len(calls),2)
            self.assertGreaterEqual(sum(clock.delays),.099)

    async def test_global_pacing_accounts_for_expensive_endpoints(self):
        with tempfile.TemporaryDirectory() as d:
            clock=Clock();io=MarketIO(infra.StateStore(d),lambda *a:({},{}),clock.wall,clock.wall,clock.sleep)
            await io.get('https://api.binance.com/api/v3/ticker/24hr')
            await io.get('https://api.binance.com/api/v3/exchangeInfo')
            self.assertEqual(clock.delays,[8])

    async def test_server_error_is_not_mislabeled_as_rate_ban(self):
        with tempfile.TemporaryDirectory() as d:
            def request(*a): raise error(500)
            io=MarketIO(infra.StateStore(d),request)
            with self.assertRaises(urllib.error.HTTPError): await io.get('https://api.binance.com/api/v3/time')
            self.assertFalse(io.cooling)


class ScannerRecovery(unittest.IsolatedAsyncioTestCase):
    def scanner(self,d): return fixtures.ScannerIntegration().scanner(d)

    async def test_eight_hour_cold_rest_seed_stream_reuse_and_bucket_rollover(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);s.stream_connected=True
            s.cfg.quality_enabled=True;s.cfg.context_history=40
            clock=Clock();clock.now=2*28800-1;calls=[]
            def request(url,params,timeout):
                calls.append(dict(params))
                tail=int(clock.now//28800)*28800000
                return [[tail+i*28800000,100,102,99,101,1,
                         tail+(i+1)*28800000-1]
                        for i in range(1-params['limit'],1)],{}
            s.market_io=MarketIO(s.store,request,clock.wall,clock.wall,clock.sleep)
            with patch('ttw_v3.time.time',side_effect=clock.wall):
                bars=await s.fetch_klines('TESTUSDT','8h')
                self.assertEqual(len(bars),40)
                self.assertEqual(s.stats['rest_candle_seeds'],1)
                tail=bars[-1];now=int(clock.now*1000)
                s.candle_feed.accept(dict(s='TESTUSDT',E=now,k=dict(
                    i='8h',t=tail.open_time,T=tail.close_time,o='100',
                    h='102',l='99',c='101.5',v='1',x=False)))
                streamed=await s.fetch_klines('TESTUSDT','8h')
                self.assertEqual(streamed[-1].close,101.5)
                self.assertEqual(len(calls),1)
                s.stream_connected=False;clock.now=2*28800
                fresh=await s.fetch_klines('TESTUSDT','8h')
                self.assertEqual(fresh[-1].open_time,2*28800000)
                self.assertEqual(len(calls),2)

    async def test_cooldown_defers_alert_without_reserving_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);s.stream_connected=True
            s.market_io.state['until']=s.market_io.wall()+600
            await s.send_watch(fixtures.live_watch())
            self.assertEqual(s.store.data['alerts'],{})

    async def test_block_clears_candidates_and_keeps_sent_monitors(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);a=fixtures.live_watch();b=fixtures.live_watch();b.key='sent';b.alerted=True
            s.watches={a.key:a,b.key:b}
            def request(*a): raise error(418,'120')
            s.market_io.request=request
            with self.assertRaises(MarketCooldown): await s._get_json(infra.BINANCE_API+'/api/v3/time')
            self.assertEqual(list(s.watches),['sent']);self.assertTrue(s.market_recovering)

    async def test_completed_history_reused_but_current_bar_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d);calls=[]
            async def get(url,params=None,**k):
                calls.append(params)
                start=params['startTime'];end=params['endTime']
                return [[t,100,101,99,100,1,t+999] for t in range(start,end+1,1000)]
            s._get_json=get
            await s.fetch_span('TESTUSDT','1s',0,2499)
            bars=await s.fetch_span('TESTUSDT','1s',0,3499)
            self.assertEqual(calls[-1]['startTime'],2000)
            self.assertEqual(len(bars),4)
            self.assertNotIn(3000,s.history_cache[('TESTUSDT','1s')])

    async def test_incomplete_history_cannot_become_verified(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.scanner(d)
            async def get(*a,**k): return []
            s._get_json=get
            with self.assertRaises(ValueError): await s.fetch_span('TESTUSDT','1s',0,999)


if __name__=='__main__': unittest.main()

