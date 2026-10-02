#!/usr/bin/env python3
"""TTW V2.0.1 — standalone Render upload.
Generated from the maintained modular V2 source.
Second-wick tip tolerance: 0.05% on either side; body contact rejected.
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
    strategy_version: str = '2.0.1'
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
TIMEFRAMES: Dict[str, Tuple[str, int, str]] = {'2H': ('2h', 1, 'native'), '3H': ('1h', 3, 'hour'), '4H': ('4h', 1, 'native'), '6H': ('6h', 1, 'native'), '12H': ('12h', 1, 'native'), '1D': ('1d', 1, 'native'), '2D': ('1d', 2, 'day'), '3D': ('3d', 1, 'native'), '5D': ('1d', 5, 'day'), '1W': ('1w', 1, 'native'), '2W': ('1w', 2, 'week'), '1M': ('1M', 1, 'native'), '2M': ('1M', 2, 'month'), '3M': ('1M', 3, 'month')}
BASE_LIMITS = {'1h': 16, '2h': 5, '4h': 5, '6h': 5, '12h': 5, '1d': 28, '3d': 5, '1w': 9, '1M': 13}

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
EXCLUDED_BASES = {'USDT', 'USDC', 'FDUSD', 'TUSD', 'DAI', 'USDE', 'USDS', 'PYUSD', 'USD1', 'BUSD', 'USDP', 'GUSD', 'FRAX', 'LUSD', 'SUSD', 'EUR', 'EURC', 'EURI', 'WBTC', 'WETH', 'STETH', 'WSTETH', 'WEETH', 'WBETH', 'RETH', 'CBETH'}
LEVERAGED_SUFFIXES = ('UP', 'DOWN', 'BULL', 'BEAR')

class UniverseMixin:

    async def refresh_universe(self) -> None:
        if self.cfg.symbol_override:
            self.symbols = [s + 'USDT' for s in self.cfg.symbol_override][:self.cfg.top_n]
            self.universe_source = 'explicit symbols'
            self.last_universe_refresh = time.time()
            return
        exchange_info = await self._get_json(f'{BINANCE_API}/api/v3/exchangeInfo')
        valid = set()
        for item in exchange_info.get('symbols', []):
            symbol = item.get('symbol', '')
            base = item.get('baseAsset', '')
            if item.get('status') == 'TRADING' and item.get('quoteAsset') == 'USDT' and item.get('isSpotTradingAllowed', True) and (base not in EXCLUDED_BASES) and (not base.endswith(LEVERAGED_SUFFIXES)):
                valid.add(symbol)
        selected: List[str] = []
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
        self.universe_source = 'market cap'
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
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get('schema_version') != 2:
                raise ValueError('Unsupported state schema')
            self.data = data

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
        return True
'Pure, versioned TTW geometry. No network, storage or Telegram dependencies.'
import math
from dataclasses import dataclass
from typing import Sequence
STRATEGY_VERSION = '2.0.1'

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

def detect_setup(*, symbol: str, timeframe: str, candles: Sequence[Candle], direction: str, tolerance_pct: float=0.05, stop_buffer_pct: float=0.1, now_ms: int):
    if direction not in {'BULLISH', 'BEARISH'}:
        raise ValueError('direction must be BULLISH or BEARISH')
    if len(candles) != 3:
        raise ValueError('Exactly three candles are required')
    if not all((math.isfinite(x) and x >= 0 for x in (tolerance_pct, stop_buffer_pct))):
        raise ValueError('Invalid strategy settings')
    c1, c2, c3 = candles
    for c in candles:
        prices = (c.open, c.high, c.low, c.close)
        if not all((math.isfinite(x) and x > 0 for x in prices)):
            return None
        if not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
            return None
        if c.close_time < c.open_time:
            return None
    if c1.close_time + 1 != c2.open_time or c2.close_time + 1 != c3.open_time:
        return None
    if not c3.open_time <= now_ms <= c3.close_time:
        return None
    if direction == 'BULLISH':
        w1, w2, w3 = (c1.low, c2.low, c3.low)
        if w1 >= min(c1.open, c1.close) or w3 >= min(c3.open, c3.close):
            return None
    else:
        w1, w2, w3 = (c1.high, c2.high, c3.high)
        if w1 <= max(c1.open, c1.close) or w3 <= max(c3.open, c3.close):
            return None
    expected2 = (w1 + w3) / 2
    contact = wick_contact(c2, direction, expected2, tolerance_pct)
    if contact is None:
        return None
    buffer = stop_buffer_pct / 100
    multiplier = 1 - buffer if direction == 'BULLISH' else 1 + buffer
    if multiplier <= 0:
        return None
    return Setup(direction, symbol, timeframe, c3.open, c3.close, w1, w2, w3, expected2, contact.tip_deviation_pct, (w3 - w1) / 2 / ((w1 + w3) / 2) * 100, c3.open_time, c3.close_time, w3 * multiplier, w2 * multiplier, contact.kind, contact.tip_deviation_pct, STRATEGY_VERSION)
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
                ax.add_patch(Rectangle((i - 0.25, min(c.open, c.close)), 0.5, height, facecolor=colour))
            else:
                ax.plot([i - 0.25, i + 0.25], [c.open, c.open], color=colour)
        first = len(candles) - 3
        ax.plot([first, first + 2], [setup.wick1, setup.wick3], color='#ffdb54', linewidth=2)
        ax.scatter([first + 1], [setup.expected_wick2], color='#ffdb54', s=30)
        ax.axhline(setup.entry, color='#45c9fa', linestyle='--', label='Candle 3 open')
        ax.axhline(setup.sl1, color='#f49a65', linestyle=':', label='SL1')
        ax.axhline(setup.sl2, color='#b98cf0', linestyle=':', label='SL2')
        ax.set_title(f'{setup.symbol} · {setup.timeframe} · {setup.direction} · LIVE', color='white')
        ax.set_xticks([first, first + 1, first + 2], ['C1', 'C2', 'C3 live'])
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
TV_INTERVAL = {'2H': '120', '3H': '180', '4H': '240', '6H': '360', '12H': '720', '1D': 'D', '2D': '2D', '3D': '3D', '5D': '5D', '1W': 'W', '2W': '2W', '1M': 'M', '2M': '2M', '3M': '3M'}

def alert_id(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def alert_payload(setup, key):
    identity = alert_id(key)
    stop1 = abs(setup.entry - setup.sl1) / setup.entry * 100
    stop2 = abs(setup.entry - setup.sl2) / setup.entry * 100

    def stop_label(price):
        protective = price < setup.entry if setup.direction == 'BULLISH' else price > setup.entry
        return '' if protective else ' [not protective from entry]'
    text = f"{('🟢' if setup.direction == 'BULLISH' else '🔴')} TTW V2 · {setup.direction}\n{setup.symbol[:-4]}/USDT · {setup.timeframe} · Candle 3 LIVE\n\nEntry reference (C3 open): {fmt_price(setup.entry)}\nCurrent: {fmt_price(setup.current_price)}\nSL1: {fmt_price(setup.sl1)} ({stop1:.2f}%){stop_label(setup.sl1)}\nSL2: {fmt_price(setup.sl2)} ({stop2:.2f}%){stop_label(setup.sl2)}\n\nWick 2: {setup.wick2_contact.replace('_', ' ').lower()}\nWick-2 tip-to-line deviation: {setup.wick2_deviation_pct:.4f}%\nSnapshot of a forming candle; review the chart. Alerts only."
    url = f'https://www.tradingview.com/chart/?symbol=BINANCE%3A{setup.symbol}&interval={TV_INTERVAL[setup.timeframe]}'
    keyboard = {'inline_keyboard': [[{'text': '✅ Valid', 'callback_data': f'v|{identity}'}, {'text': '❌ Invalid', 'callback_data': f'x|{identity}'}], [{'text': '📈 Chart', 'url': url}]]}
    return (text, keyboard)

class TelegramMixin:

    def status_text(self):
        pending = sum((r['status'] == 'pending' for r in self.store.data['alerts'].values()))
        return f"✅ TTW V2 online\nUniverse: {len(self.symbols)}/{self.cfg.top_n} non-stable Binance USDT assets\nRanking: {getattr(self, 'universe_source', 'pending')}\nTimeframes: {', '.join(TIMEFRAMES)}\nWick-2 tolerance (either side): {self.cfg.wick2_tolerance_pct:.2f}%\nPoll target: {self.cfg.scan_interval}s\nPending/uncertain deliveries: {pending}\nAlerts only."

    async def telegram_poll(self):
        while not self.stop_event.is_set():
            try:
                result = await self.telegram_call('getUpdates', {'offset': self.store.data['telegram_offset'], 'timeout': 20, 'allowed_updates': ['message', 'callback_query']}, timeout=25)
                for update in result.get('result', []):
                    msg = update.get('message', {})
                    if str(msg.get('chat', {}).get('id', '')) == self.chat_id:
                        command = (msg.get('text') or '').split(' ')[0].split('@')[0].lower()
                        if command in {'/start', '/status'}:
                            await self.telegram_call('sendMessage', {'chat_id': self.chat_id, 'text': self.status_text()})
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
        if chat == self.chat_id and len(parts) == 2 and (parts[0] in {'v', 'x'}):
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

    async def scan_symbol(self, symbol):
        try:
            bases = await self.fetch_symbol_bases(symbol)
            for timeframe in TIMEFRAMES:
                candles = self.candles_for_timeframe(bases, timeframe)
                if len(candles) < 3:
                    continue
                for direction in ('BULLISH', 'BEARISH'):
                    observed_at_ms = int(time.time() * 1000) + self.server_offset_ms
                    setup = detect_setup(symbol=symbol, timeframe=timeframe, candles=candles[-3:], direction=direction, tolerance_pct=self.cfg.wick2_tolerance_pct, stop_buffer_pct=self.cfg.stop_buffer_pct, now_ms=observed_at_ms)
                    if setup is None:
                        continue
                    key = f'{symbol}|{timeframe}|{direction}|{setup.candle3_open_time}'
                    if key in self.store.data['alerts']:
                        continue
                    snapshot = dict(alert_id=alert_id(key), setup=asdict(setup), observed_at_ms=observed_at_ms, candles=[asdict(c) for c in candles], tolerance_pct=self.cfg.wick2_tolerance_pct, stop_buffer_pct=self.cfg.stop_buffer_pct)
                    self.store.reserve(key, snapshot)
                    self.store.append('alerts-v2.jsonl', snapshot)
                    try:
                        message_id = await self.send_alert(setup, key, candles)
                        self.store.sent(key, message_id)
                        logging.info('Sent %s %s %s', symbol, timeframe, direction)
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
    asyncio.run(main())
