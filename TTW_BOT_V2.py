#!/usr/bin/env python3
"""TTW V2.2 — early rejection alerts within the hard C2 wick boundary.
Frozen C1/C2 projection; chronological C3 checks down to one second.
Wick-scaled third-touch tolerance; hard C2 boundary; live body contact rejected.
Telegram alerts only. Never places trades.
"""

from dataclasses import dataclass

@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int

@dataclass(frozen=True)
class Setup:
    direction: str
    symbol: str
    timeframe: str
    entry: float
    current_price: float
    wick1: float
    wick2: float
    wick3: float
    expected_wick2: float
    wick2_deviation_pct: float
    slope_pct_per_candle: float
    candle3_open_time: int
    candle3_close_time: int
    sl1: float
    sl2: float
    wick2_contact: str = 'TIP_NEAR'
    wick2_tip_deviation_pct: float = 0.0
    strategy_version: str = '2.2'
    touch_level: float = 0.0
    candle3_open_price: float = 0.0
    touch_time: int = 0
    impulse_distance: float = 0.0
    open_to_touch_pct: float = 0.0
    touch_tolerance: float = 0.0
    rejection_distance: float = 0.0
import os
import math
from pathlib import Path

class Config:

    def __init__(self) -> None:
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip() or None
        self.scan_interval = max(10, int(os.getenv('SCAN_INTERVAL_SECONDS', '30')))
        self.universe_refresh = max(900, int(os.getenv('UNIVERSE_REFRESH_SECONDS', '21600')))
        self.top_n = min(25, max(1, int(os.getenv('TOP_N', '25'))))
        self.wick2_tolerance_pct = float(os.getenv('WICK2_TOLERANCE_PCT', '0.05'))
        self.stop_buffer_pct = float(os.getenv('STOP_BUFFER_PCT', '0.10'))
        self.max_concurrency = min(30, max(2, int(os.getenv('MAX_CONCURRENCY', '12'))))
        self.symbol_override = [s.strip().upper().replace('/USDT', '').replace('USDT', '') for s in os.getenv('SYMBOLS', '').split(',') if s.strip()]
        self.state_dir = Path(os.getenv('STATE_DIR', 'data'))
        self.send_charts = os.getenv('SEND_CHARTS', 'true').lower() == 'true'
        self.max_open_to_touch_pct = float(os.getenv('MAX_OPEN_TO_TOUCH_PCT', '1.0'))
        self.impulse_range_fraction = float(os.getenv('IMPULSE_RANGE_FRACTION', '0.5'))
        self.max_alert_delay = int(os.getenv('MAX_ALERT_DELAY_SECONDS', '90'))
        self.max_entry_move_fraction = float(os.getenv('MAX_ENTRY_MOVE_FRACTION', '0.25'))
        self.touch_wick_fraction = float(os.getenv('TOUCH_WICK_FRACTION', '0.10'))
        self.rejection_wick_fraction = float(os.getenv('REJECTION_WICK_FRACTION', '0.10'))
        if not all(math.isfinite(v) and 0 < v <= 0.25 for v in
                   (self.touch_wick_fraction, self.rejection_wick_fraction)):
            raise ValueError('Wick fractions must be in (0, 0.25]')
        if not all(math.isfinite(v) and v > 0 for v in (
            self.max_open_to_touch_pct, self.impulse_range_fraction,
            self.max_entry_move_fraction)) or not 1 <= self.max_alert_delay <= 300:
            raise ValueError('Invalid touch-first settings')
        if not math.isfinite(self.wick2_tolerance_pct) or not math.isfinite(self.stop_buffer_pct):
            raise ValueError('Tolerance and stop buffer must be finite')
        if self.wick2_tolerance_pct < 0 or not 0 <= self.stop_buffer_pct < 100:
            raise ValueError('Invalid tolerance or stop buffer')
        if not self.telegram_chat_id:
            raise RuntimeError('TELEGRAM_CHAT_ID is required for private alert routing')
        if not self.telegram_token:
            raise RuntimeError('TELEGRAM_BOT_TOKEN is required. Put the BotFather token in the environment.')
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence, Tuple
TIMEFRAMES: Dict[str, Tuple[str, int, str]] = {'2H': ('2h', 1, 'native'), '3H': ('1h', 3, 'hour'), '4H': ('4h', 1, 'native'), '6H': ('6h', 1, 'native'), '8H': ('8h', 1, 'native'), '12H': ('12h', 1, 'native'), '16H': ('1h', 16, 'hour'), '1D': ('1d', 1, 'native'), '2D': ('1d', 2, 'day'), '3D': ('3d', 1, 'native'), '4D': ('1d', 4, 'day'), '5D': ('1d', 5, 'day'), '1W': ('1w', 1, 'native'), '2W': ('1w', 2, 'week'), '1M': ('1M', 1, 'native'), '2M': ('1M', 2, 'month'), '3M': ('1M', 3, 'month')}
BASE_LIMITS = {'1h': 128, '2h': 5, '4h': 5, '6h': 5, '8h': 5, '12h': 5, '1d': 28, '3d': 5, '1w': 9, '1M': 13}

def _month_add(dt: datetime, months: int) -> datetime:
    idx = dt.year * 12 + (dt.month - 1) + months
    return datetime(idx // 12, idx % 12 + 1, 1, tzinfo=timezone.utc)

def bucket_bounds(open_time_ms: int, factor: int, mode: str) -> Tuple[int, int]:
    dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    if mode == 'hour':
        epoch_hours = int(open_time_ms // 3600000)
        start_hours = epoch_hours // factor * factor
        start_ms = start_hours * 3600000
        end_ms = start_ms + factor * 3600000
    elif mode == 'day':
        epoch_day = datetime(1970, 1, 1, tzinfo=timezone.utc)
        day_start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        days = (day_start - epoch_day).days
        start = epoch_day + timedelta(days=days // factor * factor)
        end = start + timedelta(days=factor)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
    elif mode == 'week':
        monday = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) - timedelta(days=dt.weekday())
        anchor = datetime(1970, 1, 5, tzinfo=timezone.utc)
        weeks = (monday - anchor).days // 7
        start = anchor + timedelta(weeks=weeks // factor * factor)
        end = start + timedelta(weeks=factor)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
    elif mode == 'month':
        month_idx = dt.year * 12 + (dt.month - 1)
        grouped_idx = month_idx // factor * factor
        start = datetime(grouped_idx // 12, grouped_idx % 12 + 1, 1, tzinfo=timezone.utc)
        end = _month_add(start, factor)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
    else:
        raise ValueError(f'Unsupported aggregation mode: {mode}')
    return (start_ms, end_ms)

def validate_series(candles: Sequence[Candle]) -> List[Candle]:
    """Reject malformed or non-contiguous source data instead of fabricating bars."""
    ordered = sorted(candles, key=lambda c: c.open_time)
    for i, c in enumerate(ordered):
        if not all((math.isfinite(x) and x > 0 for x in (c.open, c.close, c.low, c.high))):
            return []
        if not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
            return []
        if c.close_time < c.open_time or not math.isfinite(c.volume) or c.volume < 0:
            return []
        if i and ordered[i - 1].close_time + 1 != c.open_time:
            return []
    return ordered

def aggregate_candles(candles: Sequence[Candle], factor: int, mode: str) -> List[Candle]:
    if factor < 1:
        raise ValueError('factor must be positive')
    candles = validate_series(candles)
    if factor == 1:
        return candles
    groups = {}
    ends = {}
    for c in candles:
        start, end = bucket_bounds(c.open_time, factor, mode)
        groups.setdefault(start, []).append(c)
        ends[start] = end
    out = []
    starts = sorted(groups)
    for start in starts:
        items = groups[start]
        if items[0].open_time != start:
            continue
        if start != starts[-1] and items[-1].close_time != ends[start] - 1:
            continue
        out.append(Candle(start, items[0].open, max((c.high for c in items)), min((c.low for c in items)), items[-1].close, sum((c.volume for c in items)), ends[start] - 1))
    return out

def fmt_price(x: float) -> str:
    if x >= 1000:
        return f'{x:,.2f}'
    if x >= 1:
        return f'{x:,.4f}'
    if x >= 0.01:
        return f'{x:.6f}'
    return f'{x:.9f}'
import os
import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
BINANCE_API = os.getenv('BINANCE_API_BASE', 'https://api.binance.com').rstrip('/')
COINGECKO_API = os.getenv('COINGECKO_API_BASE', 'https://api.coingecko.com/api/v3').rstrip('/')
TELEGRAM_API = 'https://api.telegram.org'

class TransportMixin:

    @staticmethod
    def _sync_get_json(url: str, params: Optional[dict], timeout: int):
        if params:
            url = url + ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'TTW-Crypto-Scanner/2.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.loads(r.read().decode('utf-8')), r.headers)

    @staticmethod
    def _sync_post_json(url: str, payload: dict, timeout: int):
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'TTW-Crypto-Scanner/2.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.loads(r.read().decode('utf-8')), r.headers)

    async def _get_json(self, url: str, *, params: Optional[dict]=None, timeout: int=15):
        async with self.semaphore:
            for attempt in range(3):
                try:
                    data, _headers = await asyncio.to_thread(self._sync_get_json, url, params, timeout)
                    return data
                except urllib.error.HTTPError as exc:
                    if exc.code == 429 and attempt < 2:
                        delay = float(exc.headers.get('Retry-After', '2'))
                        await asyncio.sleep(max(1.0, delay))
                        continue
                    raise
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))
            raise RuntimeError('GET failed')

    async def _post_json(self, url: str, payload: dict, timeout: int=15):
        async with self.semaphore:
            for attempt in range(3):
                try:
                    data, _headers = await asyncio.to_thread(self._sync_post_json, url, payload, timeout)
                    if not data.get('ok', True):
                        raise RuntimeError(f'Telegram/API error: {data}')
                    return data
                except urllib.error.HTTPError as exc:
                    if exc.code == 429 and attempt < 2:
                        delay = float(exc.headers.get('Retry-After', '2'))
                        await asyncio.sleep(max(1.0, delay))
                        continue
                    try:
                        detail = exc.read().decode('utf-8')
                    except Exception:
                        detail = str(exc)
                    raise RuntimeError(f'HTTP {exc.code}: request rejected') from None
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))

    async def telegram_call(self, method: str, payload: dict, timeout: int=15):
        url = f'{TELEGRAM_API}/bot{self.cfg.telegram_token}/{method}'
        try:
            if method in {'sendMessage', 'sendPhoto'}:
                async with self.semaphore:
                    result, _ = await asyncio.to_thread(self._sync_post_json, url, payload, timeout)
                if not result.get('ok'):
                    raise RuntimeError('Telegram rejected message')
                return result
            return await self._post_json(url, payload, timeout=timeout)
        except Exception:
            raise RuntimeError(f'Telegram {method} failed; credentials omitted') from None

    async def telegram_photo(self, payload, png):
        import uuid
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in payload.items():
            value = json.dumps(value) if isinstance(value, dict) else str(value)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="ttw.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + png + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = urllib.request.Request(f'{TELEGRAM_API}/bot{self.cfg.telegram_token}/sendPhoto', data=b''.join(parts), headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})

        def send():
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(send)
            if not result.get('ok'):
                raise RuntimeError('Telegram rejected image')
            return result
        except Exception:
            raise RuntimeError('Telegram photo delivery uncertain; credentials omitted') from None
from typing import List
import logging
import time
EXCLUDED_BASES = {'USDT', 'USDC', 'FDUSD', 'TUSD', 'DAI', 'USDE', 'USDS', 'PYUSD', 'USD1', 'BUSD', 'USDP', 'GUSD', 'FRAX', 'RLUSD', 'LUSD', 'SUSD', 'EUR', 'EURC', 'EURI', 'WBTC', 'WETH', 'STETH', 'WSTETH', 'WEETH', 'WBETH', 'RETH', 'CBETH', 'XAUT', 'PAXG'}
LEVERAGED_SUFFIXES = ('UP', 'DOWN', 'BULL', 'BEAR')

class UniverseMixin:

    async def refresh_universe(self) -> None:
        exchange_info = await self._get_json(f'{BINANCE_API}/api/v3/exchangeInfo')
        self.tick_sizes = {}
        for item in exchange_info.get('symbols', []):
            for f in item.get('filters', []):
                if f.get('filterType') == 'PRICE_FILTER':
                    tick = float(f.get('tickSize', 0))
                    if math.isfinite(tick) and tick > 0:
                        self.tick_sizes[item['symbol']] = tick
        if self.cfg.symbol_override:
            bases = list(dict.fromkeys('TAO' if s == 'RLUSD' else s for s in self.cfg.symbol_override))
            bases = ['TAO'] + [s for s in bases if s != 'TAO' and s not in EXCLUDED_BASES]
            self.symbols = [s + 'USDT' for s in bases][:self.cfg.top_n]
            self.universe_source = 'explicit symbols'
            self.last_universe_refresh = time.time()
            return
        valid = set()
        for item in exchange_info.get('symbols', []):
            symbol = item.get('symbol', '')
            base = item.get('baseAsset', '')
            if item.get('status') == 'TRADING' and item.get('quoteAsset') == 'USDT' and item.get('isSpotTradingAllowed', True) and (base not in EXCLUDED_BASES) and (not base.endswith(LEVERAGED_SUFFIXES)):
                valid.add(symbol)
        # Reserve one slot for the requested TAO pair when it is tradable.
        selected: List[str] = ['TAOUSDT'] if 'TAOUSDT' in valid else []
        try:
            markets = await self._get_json(f'{COINGECKO_API}/coins/markets', params={'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 75, 'page': 1, 'sparkline': 'false'}, timeout=20)
            for coin in markets:
                base = str(coin.get('symbol', '')).upper()
                pair = base + 'USDT'
                if base in EXCLUDED_BASES or base.endswith(LEVERAGED_SUFFIXES):
                    continue
                if pair in valid and pair not in selected:
                    selected.append(pair)
                    if len(selected) >= self.cfg.top_n:
                        break
        except Exception as exc:
            logging.warning('CoinGecko market-cap universe failed; using Binance quote-volume fallback: %s', exc)
        self.universe_source = 'market cap (TAO included when tradable)'
        if len(selected) < self.cfg.top_n:
            self.universe_source = 'market cap + quote volume' if selected else 'quote volume fallback'
            tickers = await self._get_json(f'{BINANCE_API}/api/v3/ticker/24hr')
            ranked = []
            for t in tickers:
                symbol = t.get('symbol', '')
                if symbol not in valid or symbol in selected:
                    continue
                try:
                    quote_volume = float(t.get('quoteVolume', 0.0))
                except (TypeError, ValueError):
                    quote_volume = 0.0
                ranked.append((quote_volume, symbol))
            ranked.sort(reverse=True)
            for _, symbol in ranked:
                selected.append(symbol)
                if len(selected) >= self.cfg.top_n:
                    break
        self.symbols = selected[:self.cfg.top_n]
        self.last_universe_refresh = time.time()
        logging.info('Universe refreshed (%d): %s', len(self.symbols), ', '.join(self.symbols))
import asyncio
import logging
from typing import Dict, List

class MarketMixin:

    async def fetch_span(self, symbol, interval, start_ms, end_ms):
        """Paginate complete UTC history; never certify a missing prefix."""
        duration = {'1h': 3600000, '1m': 60000, '1s': 1000}[interval]
        if start_ms % duration or start_ms > end_ms:
            raise ValueError('Unaligned history span')
        if (end_ms - start_ms) // duration > 5000:
            raise ValueError('History span exceeds safety bound')
        cursor, bars = start_ms, []
        while cursor <= end_ms:
            raw = await self._get_json(f'{BINANCE_API}/api/v3/klines', params={
                'symbol': symbol, 'interval': interval, 'startTime': cursor,
                'endTime': end_ms, 'limit': 1000})
            page = [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                           float(k[4]), float(k[5]), int(k[6])) for k in raw]
            if not page or page[0].open_time != cursor or not validate_series(page):
                raise ValueError('Missing/malformed chronology data')
            if any(c.close_time - c.open_time + 1 != duration or c.open_time > end_ms
                   for c in page):
                raise ValueError('Invalid chronology interval')
            bars.extend(page)
            cursor = page[-1].close_time + 1
        if not validate_series(bars) or bars[-1].open_time != end_ms // duration * duration:
            raise ValueError('Incomplete chronology span')
        return bars

    async def walk_sequence(self, symbol, bars, plan, end_ms, interval='1h',
                            running_extreme=None):
        """Resolve only possible events: hour -> minute -> second.

        No guessed OHLC path. Startup/restarts reconstruct the whole C3 prefix.
        """
        extreme = plan.candle3_open if running_extreme is None else running_extreme
        for index, bar in enumerate(bars):
            touched, impulse, new_extreme = possible_events(bar, plan, extreme)
            if not touched and not impulse:
                extreme = new_extreme
                continue
            if interval == '1s':
                event, extreme = second_event(bar, plan, extreme)
            else:
                finer = '1m' if interval == '1h' else '1s'
                children = await self.fetch_span(symbol, finer, bar.open_time,
                                                 min(bar.close_time, end_ms))
                event, extreme = await self.walk_sequence(
                    symbol, children, plan, end_ms, finer, extreme)
            if event is not None:
                if event['reason'] == 'TOUCH_FIRST':
                    for following in bars[index + 1:]:
                        event['post_high'] = max(event['post_high'], following.high)
                        event['post_low'] = min(event['post_low'], following.low)
                return event, extreme
        return None, extreme

    async def fetch_klines(self, symbol: str, interval: str) -> List[Candle]:
        raw = await self._get_json(f'{BINANCE_API}/api/v3/klines', params={'symbol': symbol, 'interval': interval, 'limit': BASE_LIMITS[interval]})
        return [Candle(open_time=int(k[0]), open=float(k[1]), high=float(k[2]), low=float(k[3]), close=float(k[4]), volume=float(k[5]), close_time=int(k[6])) for k in raw]

    async def fetch_symbol_bases(self, symbol: str) -> Dict[str, List[Candle]]:
        intervals = sorted({v[0] for v in TIMEFRAMES.values()})
        tasks = [self.fetch_klines(symbol, interval) for interval in intervals]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: Dict[str, List[Candle]] = {}
        for interval, result in zip(intervals, results):
            if isinstance(result, Exception):
                logging.warning('%s %s fetch failed: %s', symbol, interval, result)
            else:
                out[interval] = result
        return out

    def candles_for_timeframe(self, base: Dict[str, List[Candle]], label: str) -> List[Candle]:
        interval, factor, mode = TIMEFRAMES[label]
        candles = base.get(interval, [])
        if factor == 1:
            return validate_series(candles)
        return aggregate_candles(candles, factor, mode)
'Atomic local state and inspectable alert snapshots for future rule fixes.'
import json
import os
from pathlib import Path
from datetime import datetime, timezone

class StateStore:

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'state-v2.json'
        self.data = {'schema_version': 2, 'alerts': {}, 'telegram_offset': 0}
        bootstrap = os.getenv('STATE_BOOTSTRAP_FILE', '')
        source = self.path if self.path.exists() else Path(bootstrap) if bootstrap else None
        if source is not None:
            data = json.loads(source.read_text())
            if data.get('schema_version') != 2:
                raise ValueError('Unsupported state schema')
            self.data = data
        self.data.setdefault('sequence_rejections', {})
        if source is not None and source != self.path:
            # Private migration seed; never overwrite newer local state. This
            # is not a substitute for a persistent STATE_DIR on the host.
            self.save()
            logging.info('TTW_STATE_BOOTSTRAP alerts=%d subscribers=%d',
                         len(self.data['alerts']), len(self.data.get('telegram_subscribers', {})))

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(self.data, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)

    def append(self, name, payload):
        with (self.directory / name).open('a') as f:
            f.write(json.dumps({'recorded_utc': datetime.now(timezone.utc).isoformat(), **payload}, allow_nan=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def reserve(self, key, snapshot):
        self.data['alerts'][key] = {'status': 'pending', **snapshot}
        self.save()

    def sent(self, key, message_id):
        self.data['alerts'][key].update(status='sent', message_id=message_id)
        if len(self.data['alerts']) > 5000:
            ordered = sorted(self.data['alerts'], key=lambda k: self.data['alerts'][k]['setup']['candle3_open_time'])
            for old in ordered[:-5000]:
                del self.data['alerts'][old]
        self.save()

    def feedback(self, alert_id, verdict, chat_id, user_id):
        record = next((r for r in self.data['alerts'].values() if r['alert_id'] == alert_id), None)
        if record is None:
            return False
        self.append('feedback-v2.jsonl', dict(alert_id=alert_id, verdict=verdict, chat_id=chat_id, user_id=user_id, strategy_version=record['setup']['strategy_version']))
        logging.info('TTW_FEEDBACK %s', json.dumps(dict(alert_id=alert_id, verdict=verdict,
                     strategy_version=record['setup']['strategy_version'])))
        return True
'Pure, versioned TTW geometry. No network, storage or Telegram dependencies.'
import math
from dataclasses import dataclass
from typing import Sequence
STRATEGY_VERSION = '2.2'
BODY_HALF_WIDTH = 0.25

def green_c1_at_reversal_low(candles, preceding_candles):
    """Conservative green-C1 exception: a new low after a contiguous red run.

    A series means at least two immediately preceding closed red candles.
    Their closes must fall; C1 must remain the lowest of the three TTW bars.
    """
    c1 = candles[0]
    run = []
    next_open = c1.open_time
    for c in reversed(preceding_candles):
        if c.close_time + 1 != next_open:
            return False
        prices = (c.open, c.high, c.low, c.close)
        if not all((math.isfinite(p) and p > 0 for p in prices)):
            return False
        if not c.low <= c.close < c.open <= c.high:
            break
        run.append(c)
        next_open = c.open_time
    if len(run) < 2:
        return False
    chronological = list(reversed(run))
    if not all((b.close < a.close for a, b in zip(chronological, chronological[1:]))):
        return False
    return c1.low <= min((c.low for c in run + list(candles[1:])))

def line_clears_bodies(candles, direction, first_tip, last_tip):
    """Check the whole fixed segment against body rectangles, including edges."""
    slope = (last_tip - first_tip) / 2
    for i, candle in enumerate(candles):
        left, right = (max(0, i - BODY_HALF_WIDTH), min(2, i + BODY_HALF_WIDTH))
        endpoints = (first_tip + slope * left, first_tip + slope * right)
        if direction == 'BULLISH':
            if max(endpoints) >= min(candle.open, candle.close):
                return False
        elif min(endpoints) <= max(candle.open, candle.close):
            return False
    return True

GEOMETRY_WICK_FRACTION = 0.02

def actual_wick_geometry(candles, direction):
    """Check actual tip alignment against 2% of the shortest of all three wicks."""
    if len(candles) != 3 or len(validate_series(candles)) != 3:
        return False
    if direction not in {'BULLISH', 'BEARISH'}:
        return False
    bullish = direction == 'BULLISH'
    tips = [c.low if bullish else c.high for c in candles]
    lengths = [(min(c.open, c.close)-c.low if bullish else
                c.high-max(c.open, c.close)) for c in candles]
    if min(lengths) <= 0:
        return False
    midpoint = (tips[0]+tips[2])/2
    if abs(tips[1]-midpoint) > GEOMETRY_WICK_FRACTION*min(lengths)+midpoint*1e-12:
        return False
    return line_clears_bodies(candles, direction, tips[0], tips[2])

@dataclass(frozen=True)
class WickContact:
    kind: str
    gap_pct: float
    tip_deviation_pct: float

def wick_contact(candle: Candle, direction: str, line: float, tolerance_pct: float):
    """The wick tip must be near the fixed C1-to-C3 line on either side.

    A body intersection is never repaired by widening the tolerance.
    """
    if direction == 'BULLISH':
        tip, body_edge = (candle.low, min(candle.open, candle.close))
        if tip >= body_edge or line >= body_edge:
            return None
        crosses = tip <= line
    elif direction == 'BEARISH':
        tip, body_edge = (candle.high, max(candle.open, candle.close))
        if tip <= body_edge or line <= body_edge:
            return None
        crosses = tip >= line
    else:
        raise ValueError('Unknown direction')
    deviation = abs(tip - line) / line * 100
    if deviation > tolerance_pct and (not math.isclose(deviation, tolerance_pct, rel_tol=1e-10, abs_tol=1e-12)):
        return None
    if crosses:
        return WickContact('TIP_TOUCH' if tip == line else 'SLIGHT_BREACH', 0.0, deviation)
    return WickContact('NEAR_MISS', deviation, deviation)

@dataclass(frozen=True)
class TouchPlan:
    direction: str
    level: float
    tolerance: float
    impulse_distance: float
    candle3_open: float
    boundary: float = 0.0
    rejection_distance: float = 0.0

def touch_plan(candles, direction, tolerance_pct, max_gap_pct, impulse_fraction,
               preceding_candles=(), tick_size=0.0, touch_wick_fraction=0.10,
               rejection_wick_fraction=0.10):
    """Freeze C1/C2 anchors, constrain touch to C2, and size the small rejection.

    Wick fractions are starting calibration, not a backtested optimum.
    """
    if direction not in {'BULLISH', 'BEARISH'}:
        raise ValueError('Unknown direction')
    if len(candles) != 3 or len(validate_series(candles)) != 3:
        return None
    c1, c2, c3 = candles
    bullish = direction == 'BULLISH'
    w1, w2 = (c1.low, c2.low) if bullish else (c1.high, c2.high)
    level = 2 * w2 - w1
    if level <= 0:
        return None
    # The projected C3 touch cannot require a breach of C2's hard extreme.
    if (level < w2 if bullish else level > w2):
        return None
    if bullish:
        if c1.close == c1.open or (c1.close > c1.open and
                not green_c1_at_reversal_low(candles, preceding_candles)):
            return None
    elif c1.close <= c1.open:
        return None
    # C3 is provisional: its current body may be approaching the line.
    # Check its OPEN here; its completed body is checked at the close.
    opening = Candle(c3.open_time, c3.open, max(c3.open, level),
                     min(c3.open, level), c3.open, c3.volume, c3.close_time)
    if not line_clears_bodies((c1, c2, opening), direction, w1, level):
        return None
    if wick_contact(c1, direction, w1, tolerance_pct) is None or \
            wick_contact(c2, direction, w2, tolerance_pct) is None:
        return None
    gap = abs(c3.open - level) / c3.open * 100
    if gap > max_gap_pct:
        return None
    # A C3 deviation moves the midpoint by half as much. Preserve the former
    # wick-2 midpoint tolerance without moving the precomputed line.
    lengths = ((min(c1.open, c1.close) - w1, min(c2.open, c2.close) - w2)
               if bullish else
               (w1 - max(c1.open, c1.close), w2 - max(c2.open, c2.close)))
    wick_scale = min(lengths)
    tolerance = min(2 * w2 * tolerance_pct / 100,
                    max(tick_size, wick_scale * touch_wick_fraction))
    true_range = max(c2.high - c2.low, abs(c2.high - c1.close),
                     abs(c2.low - c1.close))
    distance = max(true_range * impulse_fraction, 4 * tolerance,
                   c3.open * 0.0001)
    rejection = max(2 * tick_size, wick_scale * rejection_wick_fraction)
    return TouchPlan(direction, level, tolerance, distance, c3.open, w2, rejection)

def boundary_breached(candles, direction):
    """C3's entire history must remain inside C2's extreme; no price tolerance."""
    c2, c3 = candles[-2:]
    return c3.low < c2.low if direction == 'BULLISH' else c3.high > c2.high

def rejection_ready(candles, plan, seconds, touch_time, now_ms, max_move_fraction):
    """Two completed second closes after touch show a small live rejection.

    A touch/retreat within the same second is not enough. The last completed
    second must be fresh; stale quotes or a resumed push cannot certify entry.
    """
    c1, c2, c3 = candles
    bullish = plan.direction == 'BULLISH'
    if boundary_breached(candles, plan.direction):
        return False
    if not actual_wick_geometry(candles, plan.direction):
        return False
    tip = c3.low if bullish else c3.high
    if not touch_reached(tip, plan) or abs(tip-plan.level) > plan.tolerance + plan.level*1e-12:
        return False
    if not line_clears_bodies(candles, plan.direction,
                             c1.low if bullish else c1.high, plan.level):
        return False
    completed = [b for b in seconds if b.open_time > touch_time and b.close_time < now_ms]
    if len(completed) < 2:
        return False
    pair = completed[-2:]
    if pair[0].close_time + 1 != pair[1].open_time or now_ms-pair[-1].close_time > 3000:
        return False
    maximum = plan.impulse_distance * max_move_fraction
    for price in [pair[0].close, pair[1].close, c3.close]:
        retreat = price-plan.level if bullish else plan.level-price
        recoil = price-tip if bullish else tip-price
        if retreat < plan.rejection_distance or recoil < plan.rejection_distance or retreat > maximum:
            return False
    return True

def touch_reached(price, plan):
    # C3 must reach the line. Tolerance limits overshoot; it cannot certify a near miss.
    return price <= plan.level if plan.direction == 'BULLISH' \
        else price >= plan.level

def possible_events(bar, plan, running_extreme):
    """OHLC cannot establish high/low order. Refine every possible event."""
    if plan.direction == 'BULLISH':
        extreme = min(running_extreme, bar.low)
        return touch_reached(bar.low, plan), bar.high - extreme >= plan.impulse_distance, extreme
    extreme = max(running_extreme, bar.high)
    return touch_reached(bar.high, plan), extreme - bar.low >= plan.impulse_distance, extreme

def second_event(bar, plan, running_extreme):
    """Conservatively reject unresolved order even within a one-second bar."""
    touched, impulse, extreme = possible_events(bar, plan, running_extreme)
    # The bar open is known to occur first. A gap in the reversal direction
    # at that open is an impulse before anything else in the bar.
    open_impulse = bar.open - running_extreme >= plan.impulse_distance \
        if plan.direction == 'BULLISH' else running_extreme - bar.open >= plan.impulse_distance
    if open_impulse:
        return {'reason': 'IMPULSE_BEFORE_TOUCH'}, extreme
    if touched and touch_reached(bar.open, plan):
        return dict(reason='TOUCH_FIRST', touch_time=bar.open_time,
                    post_high=bar.high, post_low=bar.low), extreme
    if touched and impulse:
        return {'reason': 'AMBIGUOUS_ORDER'}, extreme
    if impulse:
        return {'reason': 'IMPULSE_BEFORE_TOUCH'}, extreme
    if touched:
        return dict(reason='TOUCH_FIRST', touch_time=bar.open_time,
                    post_high=bar.high, post_low=bar.low), extreme
    return None, extreme

def setup_at_touch(symbol, timeframe, candles, plan, evidence, stop_buffer_pct):
    c1, c2, c3 = candles
    bullish = plan.direction == 'BULLISH'
    w1, w2, w3 = (c1.low, c2.low, c3.low) if bullish else (c1.high, c2.high, c3.high)
    multiplier = 1 - stop_buffer_pct / 100 if bullish else 1 + stop_buffer_pct / 100
    return Setup(direction=plan.direction, symbol=symbol, timeframe=timeframe,
                 entry=c3.close, current_price=c3.close, wick1=w1, wick2=w2,
                 wick3=w3, expected_wick2=(w1+w3)/2,
                 wick2_deviation_pct=abs(w2-(w1+w3)/2)/((w1+w3)/2)*100,
                 slope_pct_per_candle=(w2-w1)/w2*100,
                 candle3_open_time=c3.open_time, candle3_close_time=c3.close_time,
                 sl1=w3*multiplier, sl2=w2*multiplier,
                 wick2_contact='ANCHOR', strategy_version=STRATEGY_VERSION,
                 touch_level=plan.level, candle3_open_price=c3.open,
                 touch_time=evidence['touch_time'], impulse_distance=plan.impulse_distance,
                 open_to_touch_pct=abs(c3.open-plan.level)/c3.open*100,
                 touch_tolerance=plan.tolerance, rejection_distance=plan.rejection_distance)

def final_verdict(candles, setup, tolerance_pct):
    if len(candles) != 3 or len(validate_series(candles)) != 3:
        return 'UNVERIFIED'
    c1, c2, c3 = candles
    bullish = setup['direction'] == 'BULLISH'
    if boundary_breached(candles, setup['direction']):
        return 'INVALID: C3 breached the hard C2 wick boundary'
    if (c3.close <= c3.open if bullish else c3.close >= c3.open):
        return 'INVALID: C3 did not close in the reversal direction'
    level = setup['touch_level']
    tip = c3.low if bullish else c3.high
    if (tip > level if bullish else tip < level):
        return 'INVALID: C3 never reached the fixed touch line'
    allowed = setup.get('touch_tolerance', 2 * setup['wick2'] * tolerance_pct / 100)
    if abs(tip - level) > allowed + level * 1e-12:
        return 'INVALID: C3 wick breached the fixed touch zone'
    if not actual_wick_geometry(candles, setup['direction']):
        return 'INVALID: actual three-wick alignment or body clearance failed'
    if not line_clears_bodies(candles, setup['direction'], setup['wick1'], level):
        return 'INVALID: completed body intersects the wick line'
    return 'CONFIRMED: touch-first sequence and reversal close'

'Render the exact OHLC snapshot used by the detector.'
from io import BytesIO
import threading
_lock = threading.Lock()

def render_chart(candles, setup):
    with _lock:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
        fig.patch.set_facecolor('#121a29')
        ax.set_facecolor('#121a29')
        for i, c in enumerate(candles):
            colour = '#00b99a' if c.close >= c.open else '#f04a64'
            ax.plot([i, i], [c.low, c.high], color=colour, linewidth=1.5)
            height = abs(c.close - c.open)
            if height:
                ax.add_patch(Rectangle((i - BODY_HALF_WIDTH, min(c.open, c.close)), 2 * BODY_HALF_WIDTH, height, facecolor=colour))
            else:
                ax.plot([i - BODY_HALF_WIDTH, i + BODY_HALF_WIDTH], [c.open, c.open], color=colour)
        first = len(candles) - 3
        ax.plot([first, first + 2], [setup.wick1, setup.wick3], color='#ffdb54', linewidth=2)
        ax.scatter([first + 1], [setup.wick2], color='#ffdb54', s=30)
        ax.scatter([first + 2], [setup.wick3], color='white', marker='x', s=35)
        ax.axhline(setup.wick2, color='#ff7c7c', linestyle='-.', label='C2 hard boundary')
        ax.axhline(setup.entry, color='#45c9fa', linestyle='--', label='Price at alert')
        if setup.candle3_open_price:
            ax.axhline(setup.candle3_open_price, color='#cad3df', linestyle=':', label='Candle 3 open')
        ax.axhline(setup.sl1, color='#f49a65', linestyle=':', label='SL1')
        ax.axhline(setup.sl2, color='#b98cf0', linestyle=':', label='SL2')
        ax.set_title(f'BINANCE SPOT · {setup.symbol} · {setup.timeframe} · {setup.direction} · LIVE', color='white')
        labels = [f'C{i+1}\n' + datetime.fromtimestamp(c.open_time/1000, timezone.utc).strftime('%d %b %H:%M')
                  for i, c in enumerate(candles[-3:])]
        ax.set_xticks([first, first + 1, first + 2], labels)
        ax.set_xlabel('Candle opening times · UTC', color='#cad3df')
        ax.tick_params(colors='#cad3df')
        ax.grid(alpha=0.12)
        ax.ticklabel_format(axis='y', style='plain', useOffset=False)
        ax.legend(loc='best')
        fig.tight_layout()
        output = BytesIO()
        fig.savefig(output, format='png')
        plt.close(fig)
        return output.getvalue()
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
TV_INTERVAL = {'2H': '120', '3H': '180', '4H': '240', '6H': '360', '8H': '480', '12H': '720', '16H': '960', '1D': 'D', '2D': '2D', '3D': '3D', '4D': '4D', '5D': '5D', '1W': 'W', '2W': '2W', '1M': 'M', '2M': '2M', '3M': '3M'}

def alert_id(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def alert_payload(setup, key):
    identity = alert_id(key)
    stop1 = abs(setup.entry - setup.sl1) / setup.entry * 100
    stop2 = abs(setup.entry - setup.sl2) / setup.entry * 100

    def stop_label(price):
        protective = price < setup.entry if setup.direction == 'BULLISH' else price > setup.entry
        return '' if protective else ' [not protective from entry]'
    opened = datetime.fromtimestamp(setup.candle3_open_time/1000, timezone.utc).strftime('%d %b %H:%M UTC')
    text = f"{('🟢' if setup.direction == 'BULLISH' else '🔴')} TTW V{setup.strategy_version} · {setup.direction}\n{setup.symbol[:-4]}/USDT · {setup.timeframe} · EARLY REJECTION / LIVE\nBinance spot · C3 starts {opened}\n\nPrice at alert: {fmt_price(setup.entry)}\nC3 open: {fmt_price(setup.candle3_open_price)}\nFixed touch level: {fmt_price(setup.touch_level)}\nC2 hard boundary: {fmt_price(setup.wick2)}\nSL1: {fmt_price(setup.sl1)} ({stop1:.2f}%){stop_label(setup.sl1)}\nSL2: {fmt_price(setup.sl2)} ({stop2:.2f}%){stop_label(setup.sl2)}\n\nActual C2 tip vs C1–C3 midpoint: {setup.wick2_deviation_pct:.4f}%\nTouch then small rejection; full C3 close not awaited. Live setup can still invalidate. Alerts only."
    url = f'https://www.tradingview.com/chart/?symbol=BINANCE%3A{setup.symbol}&interval={TV_INTERVAL[setup.timeframe]}'
    keyboard = {'inline_keyboard': [[{'text': '✅ Valid', 'callback_data': f'v|{identity}'}, {'text': '❌ Invalid', 'callback_data': f'x|{identity}'}], [{'text': '📈 Chart', 'url': url}]]}
    return (text, keyboard)

class TelegramMixin:

    def recipient_ids(self):
        return list(dict.fromkeys([str(self.chat_id)] + list(
            self.store.data.get('telegram_subscribers', {}))))

    async def handle_message(self, msg):
        chat = str(msg.get('chat', {}).get('id', ''))
        if not chat:
            return
        words = (msg.get('text') or '').split()
        if not words:
            return
        command = words[0].split('@')[0].lower()
        owner = chat == str(self.chat_id)
        subscribers = self.store.data.setdefault('telegram_subscribers', {})
        invites = self.store.data.setdefault('telegram_invites', {})
        text = None
        if command == '/invite' and owner:
            import secrets
            token = secrets.token_urlsafe(24)
            now = time.time()
            invites = {k: v for k, v in invites.items() if v > now}
            me = await self.telegram_call('getMe', {})
            invites[token] = now + 86400
            self.store.data['telegram_invites'] = invites
            self.store.save()
            text = ('Share this single-use link with your friend (expires in 24 hours):\n'
                    f"https://t.me/{me['result']['username']}?start={token}")
        elif command == '/start':
            if owner or chat in subscribers:
                text = self.status_text()
            elif (msg.get('chat', {}).get('type') == 'private' and len(words) == 2
                  and invites.get(words[1], 0) > time.time()):
                del invites[words[1]]
                subscribers[chat] = {'joined_at': time.time()}
                self.store.save()
                text = '✅ You will receive new TTW alerts here. Use /stop to unsubscribe.'
            else:
                text = 'Ask the bot owner for an invite link to receive TTW alerts.'
        elif command == '/stop' and chat in subscribers:
            del subscribers[chat]
            self.store.save()
            text = 'You have unsubscribed from TTW alerts.'
        elif command == '/status' and (owner or chat in subscribers):
            text = self.status_text()
        elif command == '/subscribers' and owner:
            text = 'Additional recipients: ' + (', '.join(subscribers) or 'none')
        elif command == '/remove' and owner and len(words) == 2:
            removed = subscribers.pop(words[1], None)
            self.store.save()
            text = 'Recipient removed.' if removed else 'Recipient not found.'
        if text:
            await self.telegram_call('sendMessage', {'chat_id': chat, 'text': text})

    def status_text(self):
        pending = sum((r['status'] == 'pending' for r in self.store.data['alerts'].values()))
        return f"✅ TTW V{STRATEGY_VERSION} early rejection online\nMarket: Binance spot · UTC candles\nUniverse: {len(self.symbols)}/{self.cfg.top_n}\nRanking: {getattr(self, 'universe_source', 'pending')}\nTimeframes: {', '.join(TIMEFRAMES)}\nC2 wick: hard boundary, no breach allowance\nTouch tolerance capped at {self.cfg.touch_wick_fraction:.0%} of smaller anchor wick\nSmall rejection: {self.cfg.rejection_wick_fraction:.0%} of smaller anchor wick, at least 2 ticks\nTwo completed second closes after touch required\nPoll target: {self.cfg.scan_interval}s\nPending/uncertain deliveries: {pending}\nAlerts only."

    async def telegram_poll(self):
        while not self.stop_event.is_set():
            try:
                result = await self.telegram_call('getUpdates', {'offset': self.store.data['telegram_offset'], 'timeout': 20, 'allowed_updates': ['message', 'callback_query']}, timeout=25)
                for update in result.get('result', []):
                    msg = update.get('message', {})
                    await self.handle_message(msg)
                    cb = update.get('callback_query')
                    if cb:
                        await self.handle_callback(cb)
                    self.store.data['telegram_offset'] = int(update['update_id']) + 1
                    self.store.save()
            except Exception:
                logging.warning('Telegram polling failed; retrying without exposing credentials')
                try:
                    await asyncio.wait_for(self.stop_event.wait(), 3)
                except asyncio.TimeoutError:
                    pass

    async def handle_callback(self, cb):
        chat = str(cb.get('message', {}).get('chat', {}).get('id', ''))
        parts = (cb.get('data') or '').split('|')
        answer = 'Unrecognised feedback'
        if chat in self.recipient_ids() and len(parts) == 2 and (parts[0] in {'v', 'x'}):
            verdict = 'VALID' if parts[0] == 'v' else 'INVALID'
            if self.store.feedback(parts[1], verdict, chat, str(cb.get('from', {}).get('id', ''))):
                answer = f'Feedback recorded: {verdict.lower()}'
        if cb.get('id'):
            await self.telegram_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': answer})

    async def send_alert(self, setup, key, candles):
        text, keyboard = alert_payload(setup, key)
        png = None
        if self.cfg.send_charts:
            try:
                png = await asyncio.to_thread(render_chart, candles, setup)
            except Exception:
                logging.warning('Chart rendering failed; sending text snapshot')
        self.store.data['alerts'][key]['delivery_kind'] = 'photo' if png else 'text'
        if png:
            result = await self.telegram_photo({'chat_id': self.chat_id, 'caption': text, 'reply_markup': keyboard}, png)
        else:
            result = await self.telegram_call('sendMessage', {'chat_id': self.chat_id, 'text': text, 'reply_markup': keyboard, 'disable_web_page_preview': True})
        return result['result']['message_id']
import asyncio
import logging
import time
from dataclasses import asdict

class TTWScanner(TransportMixin, UniverseMixin, MarketMixin, TelegramMixin):

    def __init__(self, config):
        self.cfg = config
        self.stop_event = asyncio.Event()
        self.chat_id = config.telegram_chat_id
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self.store = StateStore(config.state_dir)
        self.symbols = []
        self.last_universe_refresh = 0.0
        self.server_offset_ms = 0

    def reject_sequence(self, key, reason, close_time):
        self.store.data['sequence_rejections'][key] = dict(reason=reason, close_time=close_time)
        expired = [k for k, v in self.store.data['sequence_rejections'].items()
                   if v['close_time'] < int(time.time() * 1000) - 86400000]
        for old in expired:
            del self.store.data['sequence_rejections'][old]
        self.store.save()
        logging.info('Touch-first rejected %s: %s', key, reason)

    async def close_updates(self, symbol, timeframe, candles, now_ms):
        by_open = {c.open_time: i for i, c in enumerate(candles)}
        for record in list(self.store.data['alerts'].values()):
            setup = record['setup']
            if setup.get('strategy_version') != STRATEGY_VERSION or setup['symbol'] != symbol \
                    or setup['timeframe'] != timeframe or setup['candle3_close_time'] >= now_ms:
                continue
            if record.get('close_update_sent') or record['status'] != 'sent':
                continue
            if record.get('close_update_attempts', 0) >= 3:
                continue
            i = by_open.get(setup['candle3_open_time'])
            if i is None or i < 2:
                continue
            verdict = final_verdict(candles[i-2:i+1], setup, record['tolerance_pct'])
            record['close_verdict'] = verdict
            record['closed_candle3'] = asdict(candles[i])
            record['close_update_attempts'] = record.get('close_update_attempts', 0) + 1
            self.store.save()
            text, keyboard = alert_payload(Setup(**setup), '')
            # Keep the original feedback identity and chart link.
            keyboard['inline_keyboard'][0][0]['callback_data'] = 'v|' + record['alert_id']
            keyboard['inline_keyboard'][0][1]['callback_data'] = 'x|' + record['alert_id']
            text = text.replace('EARLY REJECTION / LIVE', 'CANDLE 3 CLOSED') + '\n\n' + verdict
            photo = record.get('delivery_kind') == 'photo'
            payload = dict(chat_id=self.chat_id, message_id=record['message_id'], reply_markup=keyboard)
            payload['caption' if photo else 'text'] = text
            try:
                await self.telegram_call('editMessageCaption' if photo else 'editMessageText', payload)
                record['close_update_sent'] = True
                self.store.save()
            except Exception:
                logging.warning('C3 close update failed for %s %s; verdict retained locally', symbol, timeframe)

    async def live_invalidations(self, symbol, timeframe, candles, now_ms):
        """Mark an already sent live setup invalid as soon as the poll observes a breach."""
        if len(candles) < 3:
            return
        c3 = candles[-1]
        for record in list(self.store.data['alerts'].values()):
            setup = record['setup']
            if (setup.get('strategy_version') != STRATEGY_VERSION or
                    setup['symbol'] != symbol or setup['timeframe'] != timeframe or
                    setup['candle3_open_time'] != c3.open_time or
                    now_ms > c3.close_time or record['status'] != 'sent' or
                    record.get('live_invalidation_sent') or
                    record.get('live_invalidation_attempts', 0) >= 3):
                continue
            bullish = setup['direction'] == 'BULLISH'
            tip = c3.low if bullish else c3.high
            boundary = setup['wick2']
            if (tip < boundary if bullish else tip > boundary):
                verdict = 'INVALID: C3 breached the hard C2 wick boundary'
            elif abs(tip-setup['touch_level']) > setup['touch_tolerance'] + tip*1e-12:
                verdict = 'INVALID: C3 wick breached the fixed touch zone'
            elif not actual_wick_geometry(candles[-3:], setup['direction']):
                verdict = 'INVALID: actual three-wick alignment or body clearance failed'
            else:
                continue
            record['live_verdict'] = verdict
            record['live_invalidation_attempts'] = record.get('live_invalidation_attempts', 0) + 1
            self.store.save()
            text, keyboard = alert_payload(Setup(**setup), '')
            for button, prefix in zip(keyboard['inline_keyboard'][0], ('v|', 'x|')):
                button['callback_data'] = prefix + record['alert_id']
            text = text.replace('EARLY REJECTION / LIVE', 'INVALIDATED LIVE') + '\n\n' + verdict
            photo = record.get('delivery_kind') == 'photo'
            payload = dict(chat_id=self.chat_id, message_id=record['message_id'], reply_markup=keyboard)
            payload['caption' if photo else 'text'] = text
            try:
                await self.telegram_call('editMessageCaption' if photo else 'editMessageText', payload)
                record['live_invalidation_sent'] = True
                self.store.save()
            except Exception:
                logging.warning('Live invalidation update failed for %s %s', symbol, timeframe)

    async def scan_symbol(self, symbol):
        try:
            bases = await self.fetch_symbol_bases(symbol)
            for timeframe in TIMEFRAMES:
                candles = self.candles_for_timeframe(bases, timeframe)
                if len(candles) < 3:
                    continue
                observed_at_ms = int(time.time() * 1000) + self.server_offset_ms
                await self.close_updates(symbol, timeframe, candles, observed_at_ms)
                await self.live_invalidations(symbol, timeframe, candles, observed_at_ms)
                c1, c2, c3 = candles[-3:]
                if not c3.open_time <= observed_at_ms <= c3.close_time:
                    continue
                for direction in ('BULLISH', 'BEARISH'):
                    key = f'{symbol}|{timeframe}|{direction}|{c3.open_time}'
                    if key in self.store.data['alerts'] or key in self.store.data['sequence_rejections']:
                        continue
                    if boundary_breached(candles[-3:], direction):
                        self.reject_sequence(key, 'C2_BOUNDARY_BREACH', c3.close_time)
                        continue
                    tick_size = getattr(self, 'tick_sizes', {}).get(symbol)
                    if not tick_size:
                        # Noise filtering needs the real market tick size, never an invented one.
                        continue
                    plan = touch_plan(candles[-3:], direction, self.cfg.wick2_tolerance_pct,
                                      self.cfg.max_open_to_touch_pct, self.cfg.impulse_range_fraction,
                                      candles[:-3], tick_size,
                                      self.cfg.touch_wick_fraction, self.cfg.rejection_wick_fraction)
                    if plan is None:
                        continue
                    tip = c3.low if direction == 'BULLISH' else c3.high
                    if not touch_reached(tip, plan):
                        continue
                    if abs(tip - plan.level) > plan.tolerance + plan.level * 1e-12:
                        self.reject_sequence(key, 'WICK_OVERSHOOT', c3.close_time)
                        continue
                    try:
                        history = await self.fetch_span(symbol, '1h', c3.open_time, observed_at_ms)
                        if not math.isclose(history[0].open, c3.open, rel_tol=1e-9):
                            raise ValueError('C3 open disagrees with chronology')
                        event, _ = await self.walk_sequence(symbol, history, plan, observed_at_ms)
                    except Exception:
                        logging.warning('Sequence data unavailable for %s; no alert', key)
                        continue
                    if event is None:
                        continue
                    if event['reason'] != 'TOUCH_FIRST':
                        self.reject_sequence(key, event['reason'], c3.close_time)
                        continue
                    checked_ms = int(time.time() * 1000) + self.server_offset_ms
                    if checked_ms > c3.close_time or checked_ms - event['touch_time'] > self.cfg.max_alert_delay * 1000:
                        self.reject_sequence(key, 'STALE_FIRST_TOUCH', c3.close_time)
                        continue
                    # Refresh the live tail after the history/refinement requests.
                    # This also catches an impulse while the sequence was being fetched.
                    tail_start = observed_at_ms // 60000 * 60000
                    if checked_ms - tail_start > 300000:
                        continue
                    try:
                        tail = await self.fetch_span(symbol, '1s', tail_start, checked_ms)
                    except Exception:
                        logging.warning('Fresh price unavailable for %s; no alert', key)
                        continue
                    after_touch = [c for c in tail if c.open_time >= event['touch_time']]
                    high = max([event['post_high']] + [c.high for c in after_touch])
                    low = min([event['post_low']] + [c.low for c in after_touch])
                    moved = high - plan.level if direction == 'BULLISH' else plan.level - low
                    if moved >= plan.impulse_distance:
                        self.reject_sequence(key, 'IMPULSE_ALREADY_AFTER_TOUCH', c3.close_time)
                        continue
                    price = tail[-1].close
                    tip = min(c3.low, min(c.low for c in history), min(c.low for c in tail)) \
                        if direction == 'BULLISH' else max(c3.high, max(c.high for c in history), max(c.high for c in tail))
                    if abs(tip - plan.level) > plan.tolerance + plan.level * 1e-12:
                        self.reject_sequence(key, 'WICK_OVERSHOOT', c3.close_time)
                        continue
                    if abs(price - plan.level) > plan.impulse_distance * self.cfg.max_entry_move_fraction:
                        # First touch exists, but price is already too far away to issue a fresh alert.
                        continue
                    now_ms = int(time.time() * 1000) + self.server_offset_ms
                    if now_ms > c3.close_time or now_ms - event['touch_time'] > self.cfg.max_alert_delay * 1000:
                        self.reject_sequence(key, 'STALE_FIRST_TOUCH', c3.close_time)
                        continue
                    live = Candle(c3.open_time, c3.open,
                                  max(c3.high, max(c.high for c in history), max(c.high for c in tail)),
                                  min(c3.low, min(c.low for c in history), min(c.low for c in tail)),
                                  price, c3.volume, c3.close_time)
                    alert_candles = candles[:-1] + [live]
                    if boundary_breached(alert_candles[-3:], direction):
                        self.reject_sequence(key, 'C2_BOUNDARY_BREACH', c3.close_time)
                        continue
                    if not rejection_ready(alert_candles[-3:], plan, tail,
                                           event['touch_time'], now_ms,
                                           self.cfg.max_entry_move_fraction):
                        continue
                    setup = setup_at_touch(symbol, timeframe, alert_candles[-3:], plan,
                                           event, self.cfg.stop_buffer_pct)
                    snapshot = dict(alert_id=alert_id(key), setup=asdict(setup), observed_at_ms=now_ms,
                                    candles=[asdict(c) for c in alert_candles],
                                    sequence_evidence=event, sequence_resolution='1s',
                                    tolerance_pct=self.cfg.wick2_tolerance_pct,
                                    stop_buffer_pct=self.cfg.stop_buffer_pct,
                                    max_open_to_touch_pct=self.cfg.max_open_to_touch_pct,
                                    impulse_range_fraction=self.cfg.impulse_range_fraction)
                    snapshot.update(market='BINANCE_SPOT', tick_size=tick_size,
                                    touch_wick_fraction=self.cfg.touch_wick_fraction,
                                    rejection_wick_fraction=self.cfg.rejection_wick_fraction)
                    self.store.reserve(key, snapshot)
                    self.store.append('alerts-v2.jsonl', snapshot)
                    logging.info('TTW_AUDIT %s', json.dumps(dict(
                        key=key, strategy_version=STRATEGY_VERSION,
                        observed_at_ms=now_ms, touch_level=plan.level,
                        tolerance=plan.tolerance, candle3_high=live.high,
                        candle3_low=live.low, price_at_alert=price,
                        tick_size=tick_size, rejection_distance=plan.rejection_distance,
                        sequence_evidence=event,
                        candles=[asdict(c) for c in alert_candles[-3:]]), allow_nan=False))
                    try:
                        message_id = await self.send_alert(setup, key, alert_candles)
                        self.store.sent(key, message_id)
                        logging.info('Sent early rejection %s %s %s', symbol, timeframe, direction)
                    except Exception:
                        logging.error('Delivery uncertain for %s; retained pending to prevent duplicate', key)
        except Exception:
            logging.exception('Scan failed for %s', symbol)

    async def scanner_loop(self):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                clock_start = int(time.time() * 1000)
                clock = await self._get_json(f'{BINANCE_API}/api/v3/time')
                self.server_offset_ms = int(clock['serverTime']) - (clock_start + int(time.time() * 1000)) // 2
                if not self.symbols or time.time() - self.last_universe_refresh >= self.cfg.universe_refresh:
                    await self.refresh_universe()
                for i in range(0, len(self.symbols), 5):
                    await asyncio.gather(*(self.scan_symbol(s) for s in self.symbols[i:i + 5]))
            except Exception:
                logging.exception('Scanner cycle failed')
            elapsed = time.monotonic() - started
            if elapsed > self.cfg.scan_interval:
                logging.warning('Scan cycle %.1fs exceeds poll target', elapsed)
            try:
                await asyncio.wait_for(self.stop_event.wait(), max(1, self.cfg.scan_interval - elapsed))
            except asyncio.TimeoutError:
                pass

    async def run(self):
        await self.telegram_call('deleteWebhook', {'drop_pending_updates': False})
        await self.telegram_call('getMe', {})
        await self.refresh_universe()
        logging.info('TTW %s early rejection online; C2 hard boundary; open gap <= %.2f%%; impulse %.2f x C2 true range; poll %ss',
                     STRATEGY_VERSION, self.cfg.max_open_to_touch_pct,
                     self.cfg.impulse_range_fraction, self.cfg.scan_interval)
        tasks = [asyncio.create_task(self.telegram_poll()), asyncio.create_task(self.scanner_loop())]
        try:
            await self.stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
import asyncio
import logging
import signal

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    scanner = TTWScanner(Config())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, scanner.stop_event.set)
    await scanner.run()
if __name__ == '__main__':
    from ttw_v3 import main as v3_main
    asyncio.run(v3_main())




