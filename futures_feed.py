"""Public Binance Futures candles and causal aggregate-trade recovery.

Only the explicit equity/metal watchlist is eligible. Crypto stays on Spot.
No accounts, order endpoints, mark-price candles, or fabricated second bars.
"""
from collections import OrderedDict
from dataclasses import replace
import logging
import math
import os
import time

import TTW_BOT_V2 as infra
from market_io import MarketIO
from ttw_v3 import Scanner, FUTURES_WATCHLIST


def trade_sequence(rows, plan, running_extreme):
    """Resolve first touch versus prior impulse using ordered actual trades."""
    extreme = running_extreme
    bull = plan.direction == 'BULLISH'
    for index, row in enumerate(rows):
        price = float(row['p'])
        if (price - extreme if bull else extreme - price) >= plan.impulse_distance:
            return dict(reason='IMPULSE_BEFORE_TOUCH'), extreme
        extreme = min(extreme, price) if bull else max(extreme, price)
        if infra.touch_reached(price, plan):
            prices = [float(r['p']) for r in rows[index:]]
            return dict(reason='TOUCH_FIRST', touch_time=int(row['T']),
                        post_high=max(prices), post_low=min(prices)), extreme
    return None, extreme


class FuturesScanner(Scanner):
    market_name = 'BINANCE_FUTURES'
    api_prefix = '/fapi/v1'
    uses_btc_context = False
    stream_path = '/market/stream'

    def __init__(self, config, store=None):
        super().__init__(config, store=store)
        self.api_base = os.getenv('FUTURES_API_BASE', 'https://fapi.binance.com').rstrip('/')
        self.stream_url = os.getenv('FUTURES_STREAM_URL', 'wss://fstream.binance.com:443').rstrip('/')
        self.market_io = MarketIO(self.store, self._sync_get_json,
                                  state_key='futures_market_cooldown', headroom_limit=1800)
        self.trade_history = OrderedDict()
        self.onboard_dates = {}

    def asset_class(self, symbol):
        return 'METAL' if symbol in ('XAUUSDT', 'XAGUSDT') else 'EQUITY'

    async def refresh_universe(self):
        info = await self._get_json(self.market_url('exchangeInfo'))
        requested = list(dict.fromkeys(s+'USDT' for s in self.cfg.futures_symbols))
        allowed = {s+'USDT' for s in FUTURES_WATCHLIST}
        if not set(requested) <= allowed:
            raise ValueError('Futures watchlist may contain only the selected stocks and metals')
        valid = {}; onboard = {}
        for item in info.get('symbols', []):
            symbol = item.get('symbol')
            if (symbol not in allowed or item.get('status') != 'TRADING'
                    or item.get('quoteAsset') != 'USDT'
                    or item.get('contractType') not in ('PERPETUAL', 'TRADIFI_PERPETUAL', 'TRADFI_PERPETUAL')):
                continue
            tick = next((float(f['tickSize']) for f in item.get('filters', [])
                         if f.get('filterType') == 'PRICE_FILTER'), 0)
            if math.isfinite(tick) and tick > 0:
                valid[symbol] = tick
                onboard[symbol] = int(item.get('onboardDate', 0))
        self.symbols = [s for s in requested if s in valid]
        self.tick_sizes = valid; self.onboard_dates = onboard
        self.unavailable_symbols = [s for s in requested if s not in valid]
        self.universe_source = 'explicit Futures equities/metals'
        self.last_universe_refresh = time.time()
        logging.info('TTW_UNIVERSE market=%s active=%s unavailable=%s context=own_structure',
                     self.market_name, ','.join(self.symbols),
                     ','.join(self.unavailable_symbols) or 'none')

    async def fetch_klines(self, symbol, interval):
        if interval == '1s':
            raise ValueError('Futures does not provide Spot one-second klines')
        bars = await super().fetch_klines(symbol, interval)
        # The first native interval may predate listing and be incomplete.
        return [b for b in bars if b.open_time >= self.onboard_dates.get(symbol, 0)]

    async def fetch_span(self, symbol, interval, start_ms, end_ms):
        if interval == '1s':
            raise ValueError('Use real Futures aggregate trades for sub-minute recovery')
        return await super().fetch_span(symbol, interval, start_ms, end_ms)

    async def exact_trades(self, symbol, start_ms, end_ms):
        """Complete bounded time slice, with pagination and aggregate-ID checks."""
        now = int(time.time()*1000)+self.server_offset_ms
        if (start_ms > end_ms or end_ms-start_ms >= 3600000
                or start_ms < now-48*3600000 or end_ms >= now):
            raise ValueError('Futures trade recovery outside the supported time range')
        key = (symbol, start_ms, end_ms)
        if key in self.trade_history:
            self.trade_history.move_to_end(key)
            return list(self.trade_history[key])
        rows = []; last_id = None; last_timestamp = None
        params = dict(symbol=symbol, startTime=start_ms, endTime=end_ms, limit=1000)
        for _ in range(40):
            page = await self._get_json(self.market_url('aggTrades'), params=params)
            if not isinstance(page, list):
                raise ValueError('Invalid Futures aggregate-trade response')
            complete = len(page) < 1000
            for row in page:
                aid, stamp, price, quantity = int(row['a']), int(row['T']), float(row['p']), float(row['q'])
                if (not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity < 0
                        or (last_id is not None and aid != last_id+1)
                        or (last_timestamp is not None and stamp < last_timestamp)):
                    raise ValueError('Missing or malformed Futures aggregate trades')
                if stamp < start_ms:
                    raise ValueError('Aggregate trade precedes the requested prefix')
                if stamp > end_ms:
                    complete = True
                    break
                rows.append(dict(a=aid, T=stamp, p=price, q=quantity))
                last_id, last_timestamp = aid, stamp
            if complete:
                if rows:
                    self.trade_history[key] = tuple(rows)
                    while len(self.trade_history) > 32 or sum(len(v) for v in self.trade_history.values()) > 50000:
                        self.trade_history.popitem(last=False)
                return rows
            if last_id is None:
                raise ValueError('Aggregate-trade pagination did not advance')
            # Do not combine ID pagination with time bounds. Stop reading at
            # the first actual trade beyond the original verified cutoff.
            params = dict(symbol=symbol, fromId=last_id+1, limit=1000)
        raise ValueError('Aggregate-trade recovery exceeded its work bound')

    def validate_trade_bar(self, bar, rows):
        if not rows:
            raise ValueError('No actual trades to certify an event candle')
        prices = [r['p'] for r in rows]
        tolerance = max(bar.open*1e-9, 1e-10)
        expected = (bar.open, bar.high, bar.low, bar.close)
        recovered = (prices[0], max(prices), min(prices), prices[-1])
        if any(abs(a-b) > tolerance for a, b in zip(expected, recovered)):
            raise ValueError('Trade prefix does not match the reference candle')

    async def truncate_history(self, symbol, history, cutoff):
        if history[-1].close_time <= cutoff:
            return history
        minutes = await self.fetch_span(symbol, '1m', history[-1].open_time, cutoff)
        if minutes[-1].close_time > cutoff:
            tail = minutes[-1]
            rows = await self.exact_trades(symbol, tail.open_time, cutoff)
            if not rows or not math.isclose(rows[0]['p'], tail.open, rel_tol=1e-9):
                raise ValueError('Partial minute has no verified trade-backed open')
            prices = [r['p'] for r in rows]
            minutes[-1] = replace(tail, high=max(prices), low=min(prices),
                                  close=prices[-1], volume=sum(r['q'] for r in rows))
        h = history[-1]
        if not math.isclose(minutes[0].open, h.open, rel_tol=1e-9):
            raise ValueError('Partial-hour open mismatch')
        history[-1] = replace(h, high=max(b.high for b in minutes), low=min(b.low for b in minutes),
                              close=minutes[-1].close, volume=sum(b.volume for b in minutes))
        return history

    async def walk_sequence(self, symbol, bars, plan, end_ms, interval='1h', running_extreme=None):
        extreme = plan.candle3_open if running_extreme is None else running_extreme
        for index, bar in enumerate(bars):
            touched, impulse, new_extreme = infra.possible_events(bar, plan, extreme)
            if not touched and not impulse:
                extreme = new_extreme
                continue
            if interval == '1h':
                cutoff = min(bar.close_time, end_ms)
                children = await self.fetch_span(symbol, '1m', bar.open_time, cutoff)
                if children[-1].close_time > cutoff:
                    tail = children[-1]
                    rows = await self.exact_trades(symbol, tail.open_time, cutoff)
                    if not rows or not math.isclose(rows[0]['p'], tail.open, rel_tol=1e-9):
                        raise ValueError('Partial event minute open mismatch')
                    prices = [r['p'] for r in rows]
                    children[-1] = replace(tail, high=max(prices), low=min(prices), close=prices[-1],
                                           volume=sum(r['q'] for r in rows))
                event, extreme = await self.walk_sequence(symbol, children, plan, end_ms, '1m', extreme)
            elif interval == '1m':
                rows = await self.exact_trades(symbol, bar.open_time, min(bar.close_time, end_ms))
                self.validate_trade_bar(bar, rows)
                event, extreme = trade_sequence(rows, plan, extreme)
            else:
                raise ValueError('Unsupported Futures recovery interval')
            if event is not None:
                if event['reason'] == 'TOUCH_FIRST':
                    for following in bars[index+1:]:
                        event['post_high'] = max(event['post_high'], following.high)
                        event['post_low'] = min(event['post_low'], following.low)
                return event, extreme
        return None, extreme
