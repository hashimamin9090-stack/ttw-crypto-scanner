"""Regressions for retaining expired or excessively large REST responses."""
from io import BytesIO
import tempfile
import unittest
import urllib.error

from market_io import MarketIO, MarketCooldown
from TTW_BOT_V2 import StateStore


URL = 'https://api.binance.com/api/v3/klines'


class Clock:
    def __init__(self):
        self.now = 0.0
        self.wall_shift = 0.0

    def wall(self):
        return 1700000000 + self.now + self.wall_shift

    def mono(self):
        return self.now

    async def sleep(self, delay):
        self.now += delay


def params(symbol='BTCUSDT', interval='1h'):
    return dict(symbol=symbol, interval=interval, limit=800)


class ResponseMemory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(self.directory.name)
        self.clock = Clock()
        self.calls = []

    def make_io(self, payload_size=16, **kwargs):
        def request(url, p, timeout):
            self.calls.append((url, p))
            return dict(symbol=p.get('symbol', ''),
                        body=p.get('symbol', '') + 'x' * payload_size,
                        request=len(self.calls)), {}
        return MarketIO(self.store, request, self.clock.wall, self.clock.mono,
                        self.clock.sleep, **kwargs)

    async def test_48_hour_rollovers_do_not_retain_expired_batches(self):
        io = self.make_io()
        for hour in range(48):
            self.clock.now = hour * 3600
            await io.get(URL, params())
            self.assertEqual(len(io.cache), 1)
        self.assertEqual(len(self.calls), 48)
        self.assertEqual(io.status()['cache_expired'], 47)

    async def test_heartbeat_releases_expired_responses_without_more_rest(self):
        io = self.make_io()
        await io.get(URL, params(interval='1h'))
        await io.get(URL, params(interval='2h'))
        self.clock.now = 31
        status = io.status()
        self.assertEqual(status['cache_entries'], 1)
        self.assertGreater(status['cache_bytes'], 0)
        self.clock.now = 121
        status = io.status()
        self.assertEqual(status['cache_entries'], 0)
        self.assertEqual(status['cache_bytes'], 0)
        self.assertEqual(len(self.calls), 2)

    async def test_fresh_hits_do_not_extend_the_original_response_ttl(self):
        io = self.make_io()
        original = await io.get(URL, params())
        self.clock.now = 29
        self.assertIs(await io.get(URL, params()), original)
        self.clock.now = 30
        fresh = await io.get(URL, params())
        self.assertNotEqual(fresh['request'], original['request'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(io.cache), 1)

    async def test_wall_clock_correction_cannot_keep_an_expired_body_alive(self):
        io = self.make_io()
        await io.get(URL, params())
        self.clock.now = 31
        self.clock.wall_shift = -3600
        self.assertEqual(io.status()['cache_entries'], 0)
        self.assertEqual(io.cache_bytes, 0)

    async def test_byte_budget_evicts_least_recently_used_fresh_response(self):
        io = self.make_io(payload_size=4000, cache_max_bytes=12000)
        first = await io.get(URL, params('AAAUSDT'))
        await io.get(URL, params('BBBUSDT'))
        self.assertEqual(len(io.cache), 2)
        self.assertIs(await io.get(URL, params('AAAUSDT')), first)
        await io.get(URL, params('CCCUSDT'))
        self.assertLessEqual(io.cache_bytes, 12000)
        self.assertEqual(len(io.cache), 2)
        self.assertIs(await io.get(URL, params('AAAUSDT')), first)
        self.assertEqual(len(self.calls), 3)
        await io.get(URL, params('BBBUSDT'))
        self.assertEqual(len(self.calls), 4)
        self.assertGreater(io.stats['cache_evictions'], 0)

    async def test_oversized_response_is_returned_whole_without_retention(self):
        io = self.make_io(payload_size=4000, cache_max_bytes=1024)
        for _ in range(2):
            response = await io.get(URL, params())
            self.assertEqual(len(response['body']), 4007)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(io.cache_bytes, 0)
        self.assertEqual(len(io.cache), 0)
        self.assertEqual(io.stats['cache_oversized'], 2)

    async def test_entry_limit_still_bounds_many_small_fresh_responses(self):
        io = self.make_io(cache_max_entries=2)
        for symbol in ('AAAUSDT', 'BBBUSDT', 'CCCUSDT'):
            await io.get(URL, params(symbol))
        self.assertEqual(len(io.cache), 2)
        await io.get(URL, params('AAAUSDT'))
        self.assertEqual(len(self.calls), 4)

    async def test_exchange_cooldown_releases_cache_and_size_accounting(self):
        io = self.make_io()
        await io.get(URL, params())
        self.assertGreater(io.cache_bytes, 0)
        def blocked(*args):
            raise urllib.error.HTTPError(URL, 429, 'rate limit',
                                         {'Retry-After': '120'}, BytesIO(b'{}'))
        io.request = blocked
        with self.assertRaises(MarketCooldown):
            await io.get('https://api.binance.com/api/v3/exchangeInfo')
        self.assertEqual(len(io.cache), 0)
        self.assertEqual(io.cache_bytes, 0)

    async def test_failed_uncached_request_still_releases_expired_batches(self):
        io = self.make_io()
        await io.get(URL, params())
        self.clock.now = 31
        def failed(*args):
            raise TimeoutError('synthetic upstream timeout')
        io.request = failed
        with self.assertRaises(TimeoutError):
            await io.get(URL, dict(params(), startTime=0, endTime=999))
        self.assertEqual(len(io.cache), 0)
        self.assertEqual(io.cache_bytes, 0)

    async def test_zero_budget_returns_data_with_no_cache_retention(self):
        io = self.make_io(cache_max_bytes=0)
        await io.get(URL, params())
        await io.get(URL, params())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(io.status()['cache_bytes'], 0)
        self.assertEqual(len(io.cache), 0)


if __name__ == '__main__':
    unittest.main()
