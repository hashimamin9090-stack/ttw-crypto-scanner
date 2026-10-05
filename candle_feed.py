"""REST-seeded Binance candles maintained by native UTC kline events.

No synthetic candles: a missed final event or interval forces a REST reseed.
Trade chronology remains separately verified by the scanner.
"""
from collections import deque
from dataclasses import replace
import math
import time

from TTW_BOT_V2 import Candle, validate_series


class CandleFeed:
    def __init__(self, mono=time.monotonic):
        self.mono = mono
        self.rows = {}
        self.pending = {}
        self.seen = {}

    def clear(self, symbol=None):
        for mapping in (self.rows, self.pending, self.seen):
            for key in list(mapping):
                if symbol is None or key[0] == symbol:
                    del mapping[key]

    def seed(self, symbol, interval, bars, now_ms):
        if not bars or validate_series(bars) != bars:
            raise ValueError('Invalid candle seed')
        key = (symbol, interval)
        self.rows[key] = dict(bars=list(bars), limit=len(bars),
                              closed=bars[-1].close_time < now_ms, event=now_ms)
        for bar, closed, event in self.pending.get(key, ()):
            self._apply(key, bar, closed, event)

    def accept(self, data):
        k = data['k']; key = (data['s'], k['i'])
        bar = Candle(int(k['t']), float(k['o']), float(k['h']), float(k['l']),
                     float(k['c']), float(k['v']), int(k['T']))
        event = int(data['E'])
        if (validate_series([bar]) != [bar] or not math.isfinite(bar.volume)
                or bar.volume < 0 or not isinstance(k['x'], bool)):
            raise ValueError('Invalid streamed candle')
        # Preserve ordering; late messages must not refresh a stale feed.
        if event < self.seen.get(key, (0, 0))[0]:
            return
        self.seen[key] = (event, self.mono())
        self.pending.setdefault(key, deque(maxlen=4)).append((bar, k['x'], event))
        self._apply(key, bar, k['x'], event)

    def _apply(self, key, bar, closed, event):
        row = self.rows.get(key)
        if row is None:
            return
        tail = row['bars'][-1]
        if bar.open_time < tail.open_time:
            return
        if bar.open_time == tail.open_time:
            if bar.open != tail.open or bar.close_time != tail.close_time:
                self.rows.pop(key, None)
                return
            # REST may have captured a newer tail than a queued stream event.
            close = bar.close if event >= row['event'] or closed else tail.close
            row['bars'][-1] = replace(tail, high=max(tail.high, bar.high),
                                      low=min(tail.low, bar.low), close=close,
                                      volume=max(tail.volume, bar.volume))
            row['closed'] = row['closed'] or closed
        else:
            if not row['closed'] or bar.open_time != tail.close_time + 1:
                self.rows.pop(key, None)
                return
            row['bars'].append(bar)
            row['bars'] = row['bars'][-row['limit']:]
            row['closed'] = closed
        row['event'] = max(row['event'], event)

    def get(self, symbol, interval, now_ms):
        key = (symbol, interval)
        row = self.rows.get(key)
        seen = self.seen.get(key)
        if row is None or seen is None:
            return None
        if self.mono() - seen[1] > 10 or not -2000 <= now_ms - seen[0] <= 10000:
            return None
        bars = row['bars']
        if not bars[-1].open_time <= now_ms <= bars[-1].close_time:
            return None
        return list(bars)
