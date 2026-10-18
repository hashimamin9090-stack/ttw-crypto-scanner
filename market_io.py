"""One paced Binance REST lane with durable global cooldowns.

A cooldown never selects another host or bypasses an exchange restriction.
"""
import asyncio
from collections import Counter, OrderedDict
from email.utils import parsedate_to_datetime
import json
import logging
import math
import sys
import time
import urllib.error
from typing import NamedTuple


class MarketCooldown(RuntimeError):
    pass


class _CacheEntry(NamedTuple):
    expires_at: float
    data: object
    size_bytes: int


def _retained_bytes(value, seen=None):
    """Estimate the retained JSON object graph, counting shared objects once."""
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_retained_bytes(k, seen) + _retained_bytes(v, seen)
                    for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        size += sum(_retained_bytes(item, seen) for item in value)
    return size


class MarketIO:
    def __init__(self, store, request, wall=time.time, mono=time.monotonic, sleep=asyncio.sleep,
                 *, state_key='market_cooldown', headroom_limit=3000,
                 cache_max_bytes=32 * 1024 * 1024, cache_max_entries=512):
        self.store, self.request = store, request
        self.wall, self.mono, self.sleep = wall, mono, sleep
        self.lock = asyncio.Lock()
        self.next_slot = 0
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.cache_max_bytes = max(0, int(cache_max_bytes))
        self.cache_max_entries = max(0, int(cache_max_entries))
        self.stats = Counter()
        self.state = store.data.setdefault(state_key, {})
        self.headroom_limit = headroom_limit

    @property
    def cooling(self):
        return self.wall() < self.state.get('until', 0)

    @property
    def hard_cooling(self):
        # A successful-response usage warning pauses REST, not intact live data.
        return self.cooling and self.state.get('code') != 'USAGE_HEADROOM'

    def status(self):
        self._prune_cache()
        remaining = max(0, self.state.get('until', 0) - self.wall())
        return dict(cooldown_seconds=round(remaining),
                    cooldown_kind='exchange' if self.hard_cooling else 'headroom' if self.cooling else 'none',
                    cache_entries=len(self.cache), cache_bytes=self.cache_bytes,
                    cache_limit_bytes=self.cache_max_bytes,
                    **dict(self.stats))

    def _drop_cached(self, key):
        entry = self.cache.pop(key)
        self.cache_bytes -= entry.size_bytes

    def _prune_cache(self):
        # Streaming can avoid REST for long periods. status() also calls this
        # so expired response bodies are released during regular heartbeats.
        now = self.mono()
        expired = [key for key, entry in self.cache.items()
                   if now >= entry.expires_at]
        for key in expired:
            self._drop_cached(key)
        self.stats['cache_expired'] += len(expired)

    def _cache_response(self, key, data, ttl):
        self._prune_cache()
        if key in self.cache:
            self._drop_cached(key)
        if not self.cache_max_bytes or not self.cache_max_entries:
            return
        # Include key/container overhead. This is an object-size estimate,
        # not process RSS; shared objects across entries are counted twice.
        size = _retained_bytes((key, data)) + sys.getsizeof(_CacheEntry(0, None, 0)) + 128
        if size > self.cache_max_bytes:
            self.stats['cache_oversized'] += 1
            return
        while self.cache and (len(self.cache) >= self.cache_max_entries
                              or self.cache_bytes + size > self.cache_max_bytes):
            self._drop_cached(next(iter(self.cache)))
            self.stats['cache_evictions'] += 1
        self.cache[key] = _CacheEntry(self.mono() + ttl, data, size)
        self.cache_bytes += size

    def block(self, exc):
        now = self.wall()
        delay = None
        header = exc.headers.get('Retry-After') if exc.headers else None
        if header:
            try: delay = float(header)
            except ValueError:
                try: delay = parsedate_to_datetime(header).timestamp() - now
                except (ValueError, TypeError, OverflowError): pass
        # Some responses carry the absolute ban expiry in milliseconds.
        try:
            body = json.loads(exc.read(16384))
            if isinstance(body, dict) and body.get('retryAfter'):
                expiry = float(body['retryAfter']) / 1000
                if math.isfinite(expiry): delay = max(delay or 0, expiry-now)
        except (ValueError, TypeError, OSError): pass
        count = self.state.get('blocks', 0) + 1
        if delay is None or not math.isfinite(delay) or delay <= 0:
            delay = min(86400, (300 if exc.code == 418 else 60) * 2**min(count-1, 8))
        # Add a small margin; never shorten a longer existing cooldown.
        self.state.update(until=max(self.state.get('until',0),now+delay+2),
                          code=exc.code, blocks=count)
        self.cache.clear()
        self.cache_bytes = 0
        self.stats['rate_blocks'] += 1
        self.store.save()
        logging.warning('MARKET_COOLDOWN code=%s remaining=%ss; all Binance REST paused',
                        exc.code, round(self.state['until']-now))

    @staticmethod
    def weight(url, params=None):
        if '/fapi/' in url:
            if url.endswith('/aggTrades'): return 20
            if url.endswith('/klines'):
                limit = int((params or {}).get('limit', 500))
                return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
            return 1
        if url.endswith('/exchangeInfo'): return 20
        if url.endswith('/ticker/24hr'): return 80
        return 2 if url.endswith('/klines') else 1

    @staticmethod
    def cache_policy(url, params, now):
        if url.endswith('/time'): return 60, int(now//60)
        if not url.endswith('/klines') or 'startTime' in (params or {}): return 0, 0
        interval = params['interval']
        durations={'15m':900,'1h':3600,'2h':7200,'4h':14400,'6h':21600,'8h':28800,'12h':43200,
                   '1d':86400,'3d':259200,'1w':604800}
        # Native calendar periods: current year/month identifies monthly rollover.
        bucket = time.strftime('%Y-%m',time.gmtime(now)) if interval=='1M' else int((now-(345600 if interval=='1w' else 0))//durations[interval])
        return (30 if interval=='1h' else 120), bucket

    async def get(self, url, params=None, timeout=15):
        async with self.lock:
            self._prune_cache()
            if self.cooling:
                self.stats['cooldown_deferred'] += 1
                raise MarketCooldown('Binance REST cooldown active')
            ttl, bucket = self.cache_policy(url, params, self.wall())
            key = (url, json.dumps(params,sort_keys=True), bucket)
            item = self.cache.get(key)
            if item is not None:
                self.cache.move_to_end(key)
                self.stats['cache_hits'] += 1
                return item.data
            # 600 weight/minute pacing; no startup burst and only one request in flight.
            delay = self.next_slot-self.mono()
            if delay > 0: await self.sleep(delay)
            if self.cooling: raise MarketCooldown('Binance REST cooldown active')
            weight=self.weight(url, params)
            self.next_slot = max(self.next_slot,self.mono())+weight/10
            self.stats['requests'] += 1
            self.stats['weight_reserved'] += weight
            try:
                data, headers = await asyncio.to_thread(self.request,url,params,timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in (418,429):
                    self.block(exc)
                    raise MarketCooldown('Binance REST cooldown entered') from None
                raise
            # Observe shared-IP usage and leave headroom well below a common limit.
            used = headers.get('X-MBX-USED-WEIGHT-1M') if headers else None
            try:
                used=int(used)
                self.stats['ip_weight_1m']=used
                if used>=self.headroom_limit:
                    self.state.update(until=max(self.state.get('until',0),self.wall()+62),code='USAGE_HEADROOM')
                    self.store.save()
            except (ValueError,TypeError): pass
            if ttl:
                self._cache_response(key, data, ttl)
            return data




