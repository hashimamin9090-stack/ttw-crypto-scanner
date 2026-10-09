"""TTW 3.0: logarithmic wick geometry and observed live rejection.

Public market data only; Telegram notifications, never trade execution.
The V2 module supplies transport, universe, candles, and atomic state storage.
"""
import asyncio
import base64
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
import secrets
import signal
import ssl
import struct
import time
from urllib.parse import urlsplit

import TTW_BOT_V2 as infra
from market_io import MarketIO, MarketCooldown
from candle_feed import CandleFeed
from ttw_context import PARENTS, btc_context, entry_quality
import ttw_outcomes as outcomes

VERSION = '3.3.0'
COMPATIBLE_VERSIONS = ('3.0', '3.1', '3.1.1', '3.1.2', '3.2.0', '3.2.1', '3.2.2', '3.2.3', VERSION)
Candle = infra.Candle
TIMEFRAMES = infra.TIMEFRAMES


class Config(infra.Config):
    def __init__(self):
        super().__init__()
        # Separate names prevent old deployment settings silently imposing V2 rules.
        self.geometry_wick_fraction = float(os.getenv('V3_GEOMETRY_WICK_FRACTION', '.20'))
        self.geometry_price_cap_pct = float(os.getenv('V3_GEOMETRY_PRICE_CAP_PCT', '1.0'))
        self.max_open_gap_range = float(os.getenv('V3_MAX_OPEN_GAP_RANGE', '1.5'))
        self.stream_url = os.getenv('V3_STREAM_URL', 'wss://stream.binance.com:443').rstrip('/')
        self.quality_enabled = os.getenv('V33_QUALITY_ENABLED', 'true').lower() == 'true'
        self.context_history = 40
        self.reclaim_fraction = float(os.getenv('V33_RECLAIM_FRACTION', '.08'))
        self.level_proximity = float(os.getenv('V33_LEVEL_PROXIMITY_ATR', '.35'))
        self.min_target_r = float(os.getenv('V33_MIN_TARGET_R', '1.5'))
        self.min_room_r = float(os.getenv('V33_MIN_ROOM_R', '1.25'))
        if not all(math.isfinite(v) and low <= v <= high for v, low, high in (
                (self.reclaim_fraction, .01, .2), (self.level_proximity, .1, .75),
                (self.min_target_r, 1, 4), (self.min_room_r, .5, 3))):
            raise ValueError('Invalid V33 entry quality settings')
        if not 0 < self.geometry_wick_fraction <= .3:
            raise ValueError('V3 geometry wick fraction must be in (0, .3]')
        if not 0 < self.geometry_price_cap_pct <= 2:
            raise ValueError('V3 geometry price cap must be in (0, 2]')
        if not 0 < self.max_open_gap_range <= 3:
            raise ValueError('Invalid V3 gap setting')


def reversal_context(bars, direction, tick):
    """Accept a broader opposing approach or a completed C1/C2 pullback.

    A pullback needs two opposing bodies and net opposing movement. Wick-tip
    slope belongs to geometry, not approach: ascending lows on a bullish
    pullback and descending highs on a bearish rally remain eligible.
    C3 price cannot establish either approach route.
    Reversal-colour C1 retains the original first-colour-turn restriction.
    """
    if direction not in ('BULLISH', 'BEARISH') or len(bars) < 6:
        return dict(ok=False, reason='APPROACH_HISTORY_MISSING')
    window = bars[-6:]
    if not math.isfinite(tick) or tick <= 0 or infra.validate_series(window) != window:
        return dict(ok=False, reason='APPROACH_DATA_INVALID')
    prior, c1, c2 = window[:3], window[3], window[4]
    sign = -1 if direction == 'BULLISH' else 1
    net = sign * (prior[-1].close - prior[0].open)
    steps = sum(sign * (getattr(b, field) - getattr(a, field)) >= tick * (1-1e-8)
                for a, b in zip(prior, prior[1:]) for field in ('high', 'low'))
    reversal_colour = (c1.close > c1.open if direction == 'BULLISH' else c1.close < c1.open)
    previous_opposite = (prior[-1].close < prior[-1].open if direction == 'BULLISH'
                         else prior[-1].close > prior[-1].open)
    if reversal_colour and not previous_opposite:
        return dict(ok=False, reason='C1_REVERSAL_COLOUR_ALREADY_STARTED')
    minimum = tick * (1-1e-8)
    broader = net >= minimum and steps >= 2
    pullback = (all(sign * (c.close-c.open) >= minimum for c in (c1, c2))
                and sign * (c2.close-c1.open) >= minimum)
    if not broader and not pullback:
        return dict(ok=False, reason='NO_OPPOSING_APPROACH', opposing_steps=steps)
    return dict(ok=True, reason='REVERSAL_APPROACH' if broader else 'ANCHOR_PULLBACK',
                approach_mode='BROADER_REVERSAL' if broader else 'C1_C2_PULLBACK',
                opposing_steps=steps,
                approach_net_pct=(prior[-1].close/prior[0].open-1)*100,
                anchor_net_pct=(c2.close/c1.open-1)*100,
                c1_reversal_colour=reversal_colour)


def tips_and_lengths(bars, direction):
    bull = direction == 'BULLISH'
    tips = [b.low if bull else b.high for b in bars]
    edges = [min(b.open, b.close) if bull else max(b.open, b.close) for b in bars]
    lengths = [abs(math.log(edge / tip)) for edge, tip in zip(edges, tips)]
    return tips, edges, lengths


def line_price(first, last, x):
    return math.exp(math.log(first) + (math.log(last) - math.log(first)) * x / 2)


def clears_bodies(bars, direction, first, last):
    for i, b in enumerate(bars):
        values = [line_price(first, last, max(0, i - infra.BODY_HALF_WIDTH)),
                  line_price(first, last, min(2, i + infra.BODY_HALF_WIDTH))]
        if direction == 'BULLISH':
            if max(values) >= min(b.open, b.close):
                return False
        elif min(values) <= max(b.open, b.close):
            return False
    return True


def geometry(bars, direction, tick, fraction=.20, price_cap_pct=1.0):
    """Symmetric C2 tip miss/overshoot; scale is completed anchor wicks.

    Thresholds are explicit initial calibration, not measurements of screenshots.
    Never widen tolerance to repair a body intersection or a missing wick.
    """
    if direction not in ('BULLISH', 'BEARISH') or len(bars) != 3:
        return None
    if len(infra.validate_series(bars)) != 3 or not math.isfinite(tick) or tick <= 0:
        return None
    tips, edges, lengths = tips_and_lengths(bars, direction)
    if any(abs(t - e) < 2 * tick - tick * 1e-8 for t, e in zip(tips, edges)):
        return None
    # A forming C3 must have a visible rejection wick, not an incidental sliver.
    if lengths[2] < .25 * min(lengths[:2]):
        return None
    expected = math.sqrt(tips[0] * tips[2])
    residual = abs(math.log(tips[1] / expected))
    allowed = min(fraction * min(lengths[:2]), math.log1p(price_cap_pct / 100))
    if residual > allowed + 1e-12 or not clears_bodies(bars, direction, tips[0], tips[2]):
        return None
    return dict(expected_c2=expected, gap_pct=abs(tips[1] / expected - 1) * 100,
                wick_fraction=residual / min(lengths[:2]), allowed_log=allowed,
                c2_contact=('SLIGHT_BREACH' if
                            (tips[1] < expected if direction == 'BULLISH' else tips[1] > expected)
                            else 'NEAR_MISS' if residual > 1e-12 else 'TIP_TOUCH'),
                colour_preferred=(bars[0].close < bars[0].open if direction == 'BULLISH'
                                  else bars[0].close > bars[0].open))


@dataclass(frozen=True)
class Plan:
    direction: str
    level: float
    lower: float
    upper: float
    impulse_distance: float
    rejection_distance: float
    candle3_open: float
    tick: float
    geometry_fraction: float
    price_cap_pct: float

    @property
    def contact(self):
        return self.upper if self.direction == 'BULLISH' else self.lower


def make_plan(bars, direction, tick, fraction=.20, price_cap_pct=1.0,
              impulse_fraction=.5, rejection_fraction=.10, max_gap_range=1.5):
    if len(bars) != 3 or len(infra.validate_series(bars)) != 3 or tick <= 0:
        return None
    tips, edges, lengths = tips_and_lengths(bars, direction)
    if any(abs(t - e) < 2 * tick for t, e in zip(tips[:2], edges[:2])):
        return None
    allowed = min(fraction * min(lengths[:2]), math.log1p(price_cap_pct / 100))
    central = tips[1] ** 2 / tips[0]
    lower, upper = central * math.exp(-2 * allowed), central * math.exp(2 * allowed)
    c1, c2, c3 = bars
    true_range = max(c2.high - c2.low, abs(c2.high - c1.close), abs(c2.low - c1.close))
    gap = max(lower - c3.open, c3.open - upper, 0)
    if gap > true_range * max_gap_range:
        return None
    # At least one admissible line must clear the fixed bodies and C3's open.
    opening = replace(c3, high=c3.open, low=c3.open, close=c3.open)
    if not any(clears_bodies([c1, c2, opening], direction, tips[0], p)
               for p in (lower, central, upper)):
        return None
    scale = min(abs(edges[i] - tips[i]) for i in (0, 1))
    return Plan(direction, central, lower, upper,
                max(true_range * impulse_fraction, 8 * tick),
                max(scale * rejection_fraction, 2 * tick), c3.open,
                tick, fraction, price_cap_pct)


@dataclass
class Watch:
    key: str
    symbol: str
    timeframe: str
    bars: list
    plan: Plan
    verified_ms: int = 0
    touch_ms: int = 0
    extreme: float = 0
    post_high: float = 0
    post_low: float = 0
    reject_since: int = 0
    last_ms: int = 0
    last_price: float = 0
    failed: str = ''
    alerted: bool = False
    stream_epoch: int = 0
    shadow: set = None
    busy: bool = False
    parent_bars: list = None
    quality_reason: str = ''

    def __post_init__(self):
        self.extreme = self.plan.candle3_open
        self.shadow = set()

    def observe(self, price, timestamp):
        """Chronological trade events; every return towards the tip resets hold."""
        if timestamp < self.last_ms or (self.failed and not self.alerted):
            return
        bull, p = self.plan.direction == 'BULLISH', self.plan
        self.last_ms, self.last_price = timestamp, price
        old = self.extreme
        self.extreme = min(old, price) if bull else max(old, price)
        b = self.bars[-1]
        self.bars[-1] = replace(b, high=max(b.high, price), low=min(b.low, price), close=price)
        outside = self.extreme < p.lower - p.tick * 1e-8 if bull else self.extreme > p.upper + p.tick * 1e-8
        if outside and not self.alerted:
            self.failed = 'GEOMETRY_ZONE_EXCEEDED'
            return
        if not self.touch_ms:
            # Check reversal movement before the first observed zone contact.
            if (price - old if bull else old - price) >= p.impulse_distance:
                self.failed = 'IMPULSE_BEFORE_TOUCH'
                return
            if price <= p.upper if bull else price >= p.lower:
                self.touch_ms = timestamp
                self.post_high = self.post_low = price
        if not self.touch_ms:
            return
        self.post_high, self.post_low = max(self.post_high, price), min(self.post_low, price)
        # Wick formation must be allowed to return to the colour-change level.
        # A prior substantial body beyond that level still kills the candidate.
        moved = self.post_high - p.candle3_open if bull else p.candle3_open - self.post_low
        if moved >= p.impulse_distance and not self.alerted:
            self.failed = 'IMPULSE_ALREADY_AFTER_TOUCH'
            return
        recoil = price - self.extreme if bull else self.extreme - price
        if self.extreme != old or recoil < p.rejection_distance:
            self.reject_since = 0
        elif not self.reject_since:
            self.reject_since = timestamp

    def ready(self, now_ms, max_move=.25):
        """Require live reversal colour after touch, without a close or hold."""
        if self.failed or self.alerted or not self.verified_ms or not self.touch_ms:
            return False
        if now_ms > self.bars[-1].close_time:
            return False
        if now_ms - self.last_ms > 3000 or self.last_ms > now_ms + 1000:
            return False
        bull = self.plan.direction == 'BULLISH'
        reversal_colour = self.last_price > self.plan.candle3_open if bull else self.last_price < self.plan.candle3_open
        recoil = self.last_price - self.extreme if bull else self.extreme - self.last_price
        body_move = self.last_price - self.plan.candle3_open if bull else self.plan.candle3_open - self.last_price
        return (reversal_colour
                and recoil > 0 and 0 < body_move <= self.plan.impulse_distance * max_move
                and geometry(self.bars[-3:], self.plan.direction, self.plan.tick,
                             self.plan.geometry_fraction, self.plan.price_cap_pct) is not None)


def targets(entry, direction, timeframe):
    a, b = (3, 5) if timeframe in ('2H', '3H', '4H') else (5, 10)
    sign = 1 if direction == 'BULLISH' else -1
    return entry * (1 + sign * a / 100), entry * (1 + sign * b / 100), a, b


def make_setup(watch, stop_buffer_pct, c2_reference=False):
    requested_c2 = c2_reference
    bars, p = watch.bars[-3:], watch.plan
    tips, _, _ = tips_and_lengths(bars, p.direction)
    bull = p.direction == 'BULLISH'
    first_stop = tips[2] * (1 - stop_buffer_pct / 100 if bull else 1 + stop_buffer_pct / 100)
    first_stop = min(first_stop, tips[2] - p.tick) if bull else max(first_stop, tips[2] + p.tick)
    outer = min(tips) if bull else max(tips)
    second_stop = outer * (1 - stop_buffer_pct / 100 if bull else 1 + stop_buffer_pct / 100)
    # SL2 is always farther away; when C3 is outermost use one extra buffer.
    separation = max(p.tick, tips[2] * stop_buffer_pct / 100)
    second_stop = min(second_stop, first_stop - separation) if bull else max(second_stop, first_stop + separation)
    if c2_reference:
        c2_stop = tips[1] * (1-stop_buffer_pct/100 if bull else 1+stop_buffer_pct/100)
        c2_stop = min(c2_stop, tips[1]-p.tick) if bull else max(c2_stop, tips[1]+p.tick)
        # A sloping TTW may have already extended past C2. A stop on the
        # wrong side of entry cannot protect that trade: retain the outer
        # wick alternative and identify its actual basis in the alert.
        if c2_stop < watch.last_price if bull else c2_stop > watch.last_price:
            second_stop = c2_stop
        else:
            c2_reference = False
    if min(first_stop, second_stop) <= 0:
        raise ValueError('Stops must remain positive')
    check = geometry(bars, p.direction, p.tick, p.geometry_fraction, p.price_cap_pct)
    if not check:
        raise ValueError('Geometry changed before snapshot')
    entry = watch.last_price
    tp1, tp2, pct1, pct2 = targets(entry, p.direction, watch.timeframe)
    return dict(strategy_version=VERSION, symbol=watch.symbol, timeframe=watch.timeframe,
                direction=p.direction, entry=entry, current_price=entry,
                sl1=first_stop, sl2=second_stop, tp1=tp1, tp2=tp2,
                tp_pct1=pct1, tp_pct2=pct2, wick1=tips[0], wick2=tips[1], wick3=tips[2],
                candle3_open_time=bars[-1].open_time, candle3_close_time=bars[-1].close_time,
                touch_time=watch.touch_ms, geometry_fraction=p.geometry_fraction,
                geometry_price_cap_pct=p.price_cap_pct, tick_size=p.tick,
                stop2_basis='C2_WICK' if c2_reference else 'OUTER_WICK',
                stop2_requested_c2=requested_c2, **check)


def alert_payload(setup, key, status=''):
    symbol = setup['symbol']
    pair = symbol[:-4] + '/USDT' if symbol.endswith('USDT') else symbol
    badge = '🟢' if setup['direction'] == 'BULLISH' else '🔴'
    labelled_stops = setup.get('stop2_requested_c2', False)
    stop1_label = 'SL1 (C3)' if labelled_stops else 'SL1'
    stop2_label = ('SL2 (C2)' if setup.get('stop2_basis') == 'C2_WICK' else
                   'SL2 (outer)') if labelled_stops else 'SL2'
    text = (f"{badge} {pair} · {setup['timeframe']}" + (f' · {status}' if status else '') + '\n'
            f"Price at alert: {infra.fmt_price(setup['current_price'])}\n"
            f"Entry: {infra.fmt_price(setup['entry'])}\n"
            f"{stop1_label}: {infra.fmt_price(setup['sl1'])} ({abs(setup['sl1']/setup['entry']-1)*100:.2f}%)\n"
            f"{stop2_label}: {infra.fmt_price(setup['sl2'])} ({abs(setup['sl2']/setup['entry']-1)*100:.2f}%)\n"
            f"TP: {infra.fmt_price(setup['tp1'])}–{infra.fmt_price(setup['tp2'])} "
            f"({setup['tp_pct1']}–{setup['tp_pct2']}%)")
    identity = infra.alert_id(key)
    url = f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}&interval={infra.TV_INTERVAL[setup['timeframe']]}"
    return text, {'inline_keyboard': [
        [{'text': '✅ Valid', 'callback_data': 'v|' + identity},
         {'text': '❌ Invalid', 'callback_data': 'x|' + identity}],
        [{'text': '📈 Chart', 'url': url}]]}


def render_chart(bars, setup):
    # Keep the TTW visible; the full causal history stays in the audit.
    bars = bars[-12:]
    with infra._lock:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        from matplotlib.ticker import FuncFormatter
        fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
        fig.patch.set_facecolor('#121a29'); ax.set_facecolor('#121a29')
        ax.set_yscale('log')
        for i, b in enumerate(bars):
            colour = '#00b99a' if b.close >= b.open else '#f04a64'
            ax.plot([i, i], [b.low, b.high], color=colour, linewidth=1.5)
            height = abs(b.close - b.open)
            if height:
                ax.add_patch(Rectangle((i - infra.BODY_HALF_WIDTH, min(b.open, b.close)),
                                      2 * infra.BODY_HALF_WIDTH, height, facecolor=colour))
            else:
                ax.plot([i-.25, i+.25], [b.open, b.open], color=colour)
        first = len(bars) - 3
        ax.plot([first, first+2], [setup['wick1'], setup['wick3']], color='#ffdb54', linewidth=2)
        ax.scatter([first+1], [setup['wick2']], color='#ffdb54', s=25)
        ax.axhline(setup['entry'], color='#45c9fa', linestyle='--', label='Entry / alert price')
        ax.axhline(setup['sl1'], color='#f49a65', linestyle=':', label='SL1')
        ax.axhline(setup['sl2'], color='#b98cf0', linestyle=':', label='SL2')
        # TP range stays in the caption: including distant targets would squash wick detail.
        ax.set_title(f"BINANCE SPOT · {setup['symbol']} · {setup['timeframe']} · {setup['direction']} · LOG", color='white')
        labels = [f'C{i+1}\n' + datetime.fromtimestamp(b.open_time/1000, timezone.utc).strftime('%d %b %H:%M')
                  for i, b in enumerate(bars[-3:])]
        ax.set_xticks([first, first+1, first+2], labels)
        ax.set_xlabel('Candle openings · UTC', color='#cad3df')
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: infra.fmt_price(x)))
        ax.yaxis.set_minor_formatter(FuncFormatter(lambda x, _: infra.fmt_price(x)))
        ax.tick_params(which='both', colors='#cad3df'); ax.grid(alpha=.12); ax.legend(loc='best')
        fig.tight_layout()
        output = infra.BytesIO(); fig.savefig(output, format='png'); plt.close(fig)
        return output.getvalue()


class WebSocket:
    """Small RFC6455 client for Binance's public JSON market stream.

    No optional runtime dependency or credentials. Supports fragmented messages,
    server pings and bounded payloads. Disconnects require verified REST recovery.
    """
    async def connect(self, url):
        u = urlsplit(url)
        if u.scheme != 'wss' or not u.hostname:
            raise ValueError('Market stream must use wss')
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(
            u.hostname, u.port or 443, ssl=ssl.create_default_context(), server_hostname=u.hostname), 15)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = u.path or '/'
        if u.query: path += '?' + u.query
        request = (f'GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n'
                   f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.writer.write(request.encode()); await self.writer.drain()
        header = await asyncio.wait_for(self.reader.readuntil(b'\r\n\r\n'), 15)
        lines = header.decode('ascii').split('\r\n')
        fields = {name.lower(): value.strip() for name, value in
                  (line.split(':', 1) for line in lines[1:] if ':' in line)}
        accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if ' 101 ' not in lines[0] or fields.get('sec-websocket-accept', '').strip() != accept:
            await self.close(); raise ValueError('WebSocket handshake rejected')

    async def send(self, opcode, data=b''):
        mask = secrets.token_bytes(4); size = len(data)
        prefix = bytes([0x80 | opcode])
        prefix += bytes([0x80 | size]) if size < 126 else bytes([0xfe]) + struct.pack('!H', size)
        self.writer.write(prefix + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))
        await self.writer.drain()

    async def messages(self):
        payload = bytearray(); fragmented = False
        while True:
            a, b = await asyncio.wait_for(self.reader.readexactly(2), 50)
            if a & 0x70 or b & 0x80:
                raise ValueError('Unexpected WebSocket extension/mask')
            final, opcode, size = bool(a & 0x80), a & 0xf, b & 0x7f
            if size == 126: size = struct.unpack('!H', await self.reader.readexactly(2))[0]
            if size == 127: size = struct.unpack('!Q', await self.reader.readexactly(8))[0]
            if size > 2_000_000 or (opcode >= 8 and (not final or size > 125)):
                raise ValueError('Invalid/oversized WebSocket frame')
            data = await self.reader.readexactly(size)
            if opcode == 8: return
            if opcode == 9:
                await self.send(10, data); continue
            if opcode == 10: continue
            if opcode == 1:
                if fragmented: raise ValueError('Interrupted fragmented message')
                payload = bytearray(data); fragmented = not final
            elif opcode == 0 and fragmented:
                payload.extend(data); fragmented = not final
            else: raise ValueError('Unexpected WebSocket opcode')
            if len(payload) > 2_000_000: raise ValueError('Oversized message')
            if final:
                yield json.loads(payload.decode('utf-8'))
                payload.clear()

    async def close(self):
        if getattr(self, 'writer', None):
            self.writer.close()
            try: await self.writer.wait_closed()
            except Exception: pass


class Scanner(infra.TransportMixin, infra.UniverseMixin, infra.MarketMixin, infra.TelegramMixin):
    async def fetch_klines(self, symbol, interval):
        now = int(time.time()*1000) + self.server_offset_ms
        cached = self.candle_feed.get(symbol, interval, now) if self.stream_connected else None
        if cached is not None:
            self.stats['stream_candle_hits'] += 1
            return cached
        generation = self.stream_epoch
        # Six setup/context candles, plus two aggregate buckets for alignment.
        factor = max((v[1] for v in TIMEFRAMES.values() if v[0] == interval), default=1)
        history = getattr(self.cfg, 'context_history', 8) if getattr(self.cfg, 'quality_enabled', False) else 8
        limit = max(infra.BASE_LIMITS.get(interval, 0), history * factor, 96 if interval == '15m' else 0)
        raw = await self._get_json(f'{infra.BINANCE_API}/api/v3/klines',
                                  params=dict(symbol=symbol, interval=interval, limit=limit))
        bars = [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                       float(k[4]), float(k[5]), int(k[6])) for k in raw]
        seeded_at = int(time.time()*1000)+self.server_offset_ms
        if (generation == self.stream_epoch and self.stream_connected and bars
                and bars[-1].open_time <= seeded_at <= bars[-1].close_time):
            self.candle_feed.seed(symbol, interval, bars, seeded_at)
            self.stats['rest_candle_seeds'] += 1
        return bars

    def __init__(self, config):
        self.cfg = config; self.stop_event = asyncio.Event(); self.chat_id = config.telegram_chat_id
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self.store = infra.StateStore(config.state_dir)
        self.market_io = MarketIO(self.store, self._sync_get_json)
        self.candle_feed = CandleFeed()
        self.last_clock_sync = 0
        self.verify_retry_at = {}
        self.history_cache = {}
        self.history_locks = {}
        self.market_recovering = False
        self.symbols = []; self.last_universe_refresh = 0; self.server_offset_ms = 0
        self.watches = {}; self.buffers = {}; self.stream_epoch = 0
        self.stream_connected = False; self.stream_symbols = (); self.stats = Counter()
        self.context_log = {}  # One latest decision per pair/timeframe/direction.
        self.symbol_last_id = {}; self.symbol_epoch = {}; self.tick_sizes = {}
        self.last_summary = 0
        self.pending_sends = set()
        self.verification_jobs = set()
        self.btc_bars = {'15m': [], '1h': []}
        self.last_quotes = {}
        self.outcome_keys = {}
        self.shadow_keys = {}
        self.dirty_outcomes = set()
        self.dirty_shadows = set()
        self.last_outcome_flush = 0
        self.boot_ms = int(time.time()*1000)
        self.store.data.setdefault('shadow_entries', {})
        for key, record in self.store.data['alerts'].items():
            if record.get('trade_tracking'):
                if outcomes.mark_gap(record, self.boot_ms, 'PROCESS_RESTART'):
                    self.dirty_outcomes.add(key)
                self.outcome_keys.setdefault(record['setup']['symbol'], set()).add(key)
        for key, record in self.store.data['shadow_entries'].items():
            if outcomes.mark_gap(record, self.boot_ms, 'PROCESS_RESTART'):
                self.dirty_shadows.add(key)
            if outcomes.open_paths(record['trade_tracking']):
                self.shadow_keys.setdefault(record['setup']['symbol'], set()).add(key)

    def market_symbols(self):
        return list(dict.fromkeys(self.symbols+list(self.outcome_keys)+list(self.shadow_keys)+
                    (['BTCUSDT'] if getattr(self.cfg, 'quality_enabled', False) else [])))

    def quality_check(self, watch, setup, now):
        if not getattr(self.cfg, 'quality_enabled', False):
            return dict(ok=True, reason='LEGACY_TRIGGER')
        quote = self.last_quotes.get('BTCUSDT', (0, 0))
        btc = btc_context(self.btc_bars['15m'], self.btc_bars['1h'], quote[1], quote[0], now)
        result = entry_quality(watch.bars, watch.parent_bars, setup, btc,
                               self.cfg.reclaim_fraction, self.cfg.level_proximity,
                               self.cfg.min_target_r, self.cfg.min_room_r)
        if watch.quality_reason != result['reason']:
            watch.quality_reason = result['reason']
            self.stats['quality_'+result['reason']] += 1
            logging.info('TTW_QUALITY %s %s', watch.key, json.dumps(result, allow_nan=False))
        return result

    async def context_loop(self):
        while not self.stop_event.is_set():
            if self.stream_connected:
                for interval in ('15m', '1h'):
                    try:
                        self.btc_bars[interval] = await self.fetch_klines('BTCUSDT', interval)
                    except MarketCooldown:
                        pass
                    except Exception:
                        logging.warning('BTC context seed deferred for %s', interval)
            await asyncio.sleep(5)

    def outcome_gap(self, symbol, now, reason):
        for field, index, dirty in [('alerts', self.outcome_keys, self.dirty_outcomes),
                                    ('shadow_entries', self.shadow_keys, self.dirty_shadows)]:
            for key in index.get(symbol, ()):
                record = self.store.data[field].get(key)
                if record and outcomes.mark_gap(record, now, reason):
                    dirty.add(key)

    def record_shadow(self, watch, quality):
        """One hypothetical legacy entry per identity, with no Telegram delivery.

        This denominator exposes missed winners and absolute opportunity counts;
        reporting only accepted alerts could make a restrictive filter look good.
        """
        if (not getattr(self.cfg, 'quality_enabled', False) or
                watch.key in self.store.data['shadow_entries']):
            return
        setup = make_setup(watch, self.cfg.stop_buffer_pct)
        record = dict(setup=setup, hypothetical=True, baseline='3.2.3_ENTRY_RULES',
                      first_quality=quality, observed_at_ms=watch.last_ms,
                      trade_tracking=outcomes.start_tracking(setup, watch.last_ms,
                                             self.symbol_last_id.get(watch.symbol)))
        rows = self.store.data['shadow_entries']
        rows[watch.key] = record
        # Retain open observations and the most recent 5,000 completed ones.
        completed = [k for k, r in rows.items() if not outcomes.open_paths(r['trade_tracking'])]
        for old in completed[:-5000]:
            del rows[old]
        self.shadow_keys.setdefault(watch.symbol, set()).add(watch.key)
        self.store.save()
        logging.info('TTW_SHADOW_ENTRY_V33 %s', json.dumps(dict(key=watch.key, **record), allow_nan=False))

    def flush_shadows(self, now):
        rows = self.store.data['shadow_entries']
        for symbol, keys in list(self.shadow_keys.items()):
            for key in list(keys):
                record = rows.get(key)
                if not record:
                    keys.discard(key); continue
                if outcomes.expire(record, now):
                    self.dirty_shadows.add(key)
                if not outcomes.open_paths(record['trade_tracking']):
                    keys.discard(key)
            if not keys:
                self.shadow_keys.pop(symbol, None)
        for key in self.dirty_shadows:
            record = rows.get(key)
            if not record: continue
            summary = dict(key=key, hypothetical=True, baseline=record['baseline'],
                           first_quality_reason=record['first_quality']['reason'],
                           delivered=bool(self.store.data['alerts'].get(key, {}).get('deliveries')),
                           observed_at_ms=now, **outcomes.summary(record))
            self.store.append('shadow-outcomes-v33.jsonl', summary)
            logging.info('TTW_SHADOW_OUTCOME_V33 %s', json.dumps(summary, allow_nan=False))
        if self.dirty_shadows:
            self.store.save()
            self.dirty_shadows.clear()

    async def flush_outcomes(self, now):
        self.flush_shadows(now)
        for symbol, keys in list(self.outcome_keys.items()):
            for key in list(keys):
                record = self.store.data['alerts'].get(key)
                if record is None:
                    keys.discard(key); continue
                if outcomes.expire(record, now):
                    self.dirty_outcomes.add(key)
                if not outcomes.open_paths(record['trade_tracking']):
                    keys.discard(key)
            if not keys:
                self.outcome_keys.pop(symbol, None)
        for key in list(self.dirty_outcomes):
            record = self.store.data['alerts'].get(key)
            if not record:
                self.dirty_outcomes.discard(key); continue
            tracking = record['trade_tracking']
            if tracking.get('logged_revision', -1) != tracking['revision']:
                summary = dict(key=key, strategy_version=record['setup']['strategy_version'],
                               observed_at_ms=now, **outcomes.summary(record))
                self.store.append('outcomes-v33.jsonl', summary)
                logging.info('TTW_OUTCOME_V33 %s', json.dumps(summary, allow_nan=False))
                tracking['logged_revision'] = tracking['revision']
                tracking['edit_attempts'] = 0
            self.store.save()
            if record['status'] == 'sent':
                try:
                    await self.edit_record(key, record, outcomes.status_text(record))
                except Exception:
                    if tracking.get('edit_attempts', 0) < 3:
                        tracking['edit_attempts'] = tracking.get('edit_attempts', 0)+1
                        continue
                    logging.warning('Outcome message edit deferred; result retained %s', key)
            self.dirty_outcomes.discard(key)
        if self.dirty_outcomes:
            self.store.save()

    async def _get_json(self, url, *, params=None, timeout=15):
        if urlsplit(url).netloc == urlsplit(infra.BINANCE_API).netloc:
            try:
                result = await self.market_io.get(url, params, timeout)
            except MarketCooldown:
                if self.market_io.hard_cooling:
                    self.market_recovering = True
                    # Actual exchange restrictions still require verified recovery.
                    for key,w in list(self.watches.items()):
                        if not w.alerted: self.watches.pop(key,None)
                raise
            if self.market_io.hard_cooling:
                self.market_recovering = True
                for key,w in list(self.watches.items()):
                    if not w.alerted: self.watches.pop(key,None)
            return result
        return await super()._get_json(url, params=params, timeout=timeout)

    async def fetch_span(self, symbol, interval, start_ms, end_ms):
        # Share immutable completed history across candidate verifications.
        duration={'1h':3600000,'1m':60000,'1s':1000}[interval]
        if start_ms % duration or start_ms>end_ms or (end_ms-start_ms)//duration>5000:
            raise ValueError('Invalid history span')
        key=(symbol,interval)
        lock=self.history_locks.setdefault(key,asyncio.Lock())
        async with lock:
            cache=self.history_cache.setdefault(key,{})
            cursor=start_ms; prefix=[]
            while cursor<=end_ms and cursor in cache:
                prefix.append(cache[cursor]);cursor+=duration
            tail=await super().fetch_span(symbol,interval,cursor,end_ms) if cursor<=end_ms else []
            for bar in tail:
                if bar.close_time<=end_ms: cache[bar.open_time]=bar
            if len(cache)>8192:
                for old in sorted(cache)[:-8192]: del cache[old]
            if len(self.history_cache)>100:
                old=next(k for k in self.history_cache if k!=key)
                self.history_cache.pop(old,None)
            bars=prefix+tail
            if not bars or infra.validate_series(bars)!=bars:
                raise ValueError('Incomplete cached history')
            return bars

    def status_text(self):
        return (f'TTW {VERSION} · log geometry\nPairs: {len(self.symbols)}\n'
                f'Live stream: {"connected" if self.stream_connected else "recovering"}\n'
                f'Market data: {"exchange cooldown" if self.market_io.hard_cooling else "recovering" if self.market_recovering else "REST headroom pause; live stream active" if self.market_io.cooling else "available"}\n'
                f'Watching: {len(self.watches)}\nAlerts: {self.stats["sent"]}\n'
                f'Entry context: {"BTC + levels + reclaim" if getattr(self.cfg, "quality_enabled", False) else "legacy"}\n'
                f'Outcome tracking: {sum(len(v) for v in self.outcome_keys.values())} live')

    def reject(self, watch, reason):
        if watch.alerted: return
        self.stats[reason] += 1
        self.store.data['sequence_rejections'][watch.key] = dict(
            reason=reason, close_time=watch.bars[-1].close_time, strategy_version=VERSION)
        self.store.save(); self.watches.pop(watch.key, None)
        logging.info('TTW_REJECT %s %s', watch.key, reason)

    def accept_trade(self, symbol, agg_id, timestamp, price):
        if symbol not in self.market_symbols() or not math.isfinite(price) or price <= 0: return
        previous = self.symbol_last_id.get(symbol)
        if previous is not None and agg_id <= previous: return
        if previous is not None and agg_id != previous + 1:
            self.stats['stream_gaps'] += 1
            self.symbol_epoch[symbol] = self.symbol_epoch.get(symbol, 0) + 1
            self.buffers[symbol] = deque(maxlen=10000)
            self.candle_feed.clear(symbol)
            for w in list(self.watches.values()):
                if w.symbol == symbol and not w.alerted:
                    self.watches.pop(w.key, None)
            logging.warning('Market event gap for %s; verification will restart', symbol)
            self.outcome_gap(symbol, timestamp, 'TRADE_SEQUENCE_GAP')
        self.symbol_last_id[symbol] = agg_id
        self.last_quotes[symbol] = (timestamp, price)
        for key in self.outcome_keys.get(symbol, ()):
            record = self.store.data['alerts'].get(key)
            if record and outcomes.observe(record, price, timestamp, agg_id):
                self.dirty_outcomes.add(key)
        for key in self.shadow_keys.get(symbol, ()):
            record = self.store.data['shadow_entries'].get(key)
            if record and outcomes.observe(record, price, timestamp, agg_id):
                self.dirty_shadows.add(key)
        self.buffers.setdefault(symbol, deque(maxlen=10000)).append((timestamp, price))
        for w in list(self.watches.values()):
            if w.symbol == symbol and timestamp <= w.bars[-1].close_time:
                if w.verified_ms and timestamp > w.verified_ms:
                    w.observe(price, timestamp)
                    record = self.store.data['alerts'].get(w.key)
                    if w.alerted and record:
                        record['post_alert_high'] = max(record.get('post_alert_high', record['setup']['entry']), price)
                        record['post_alert_low'] = min(record.get('post_alert_low', record['setup']['entry']), price)
                elif not w.verified_ms:
                    b = w.bars[-1]
                    w.bars[-1] = replace(b, high=max(b.high,price), low=min(b.low,price), close=price)
                    near = price <= w.plan.upper if w.plan.direction == 'BULLISH' else price >= w.plan.lower
                    if (near and not w.busy and len(self.verification_jobs) < 2
                            and not self.market_io.cooling
                            and time.monotonic() >= self.verify_retry_at.get(w.key, 0)):
                        w.busy = True
                        task = asyncio.create_task(self.verify_guarded(w))
                        self.verification_jobs.add(task)
                        task.add_done_callback(self.verification_jobs.discard)

    async def stream_loop(self):
        delay = 1
        while not self.stop_event.is_set():
            if not self.symbols:
                await asyncio.sleep(.5); continue
            ws = WebSocket()
            try:
                selected = tuple(self.market_symbols())
                intervals = sorted({v[0] for v in TIMEFRAMES.values()})
                streams = [s.lower()+suffix for s in selected for suffix in
                           ['@aggTrade']+['@kline_'+i for i in intervals]]
                if getattr(self.cfg, 'quality_enabled', False):
                    streams.append('btcusdt@kline_15m')
                path = '/stream?streams=' + '/'.join(streams)
                await ws.connect(self.cfg.stream_url + path)
                self.stream_epoch += 1; self.stream_symbols = selected
                self.symbol_last_id.clear(); self.buffers.clear()
                self.candle_feed.clear()
                self.last_quotes.clear()
                for symbol in list(self.outcome_keys):
                    self.outcome_gap(symbol, int(time.time()*1000)+self.server_offset_ms, 'STREAM_RECONNECT')
                for key, w in list(self.watches.items()):
                    if not w.alerted: self.watches.pop(key, None)
                self.stream_connected = True; delay = 1
                logging.info('TTW_STREAM connected: %d pairs, %d trade/candle streams', len(selected), len(streams))
                connected_at = time.monotonic()
                async for message in ws.messages():
                    if self.stop_event.is_set() or tuple(self.market_symbols()) != selected or time.monotonic()-connected_at > 23*3600:
                        break
                    data = message.get('data', message)
                    if data.get('e') == 'aggTrade':
                        self.accept_trade(data['s'], int(data['a']), int(data['T']), float(data['p']))
                    elif data.get('e') == 'kline':
                        self.candle_feed.accept(data)
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.stats['stream_reconnects'] += 1
                logging.warning('Market stream disconnected (%s); alerts paused until verified recovery',
                                type(exc).__name__)
            finally:
                for symbol in set(self.outcome_keys) | set(self.shadow_keys):
                    self.outcome_gap(symbol, int(time.time()*1000)+self.server_offset_ms, 'STREAM_DISCONNECT')
                self.stream_connected = False; await ws.close()
                self.candle_feed.clear()
            await asyncio.sleep(delay); delay = min(30, delay*2)

    async def verify_watch(self, watch):
        """Recover complete pre-stream chronology; never invent within-second order."""
        # Every retry rebuilds evidence from scratch; partial prior attempts cannot
        # retain a touch or a live colour turn through a failed recovery.
        watch.verified_ms = watch.touch_ms = watch.reject_since = 0
        watch.post_high = watch.post_low = 0
        watch.failed = ''
        cutoff = (int(time.time()*1000)+self.server_offset_ms)//1000*1000-1
        generation = self.stream_epoch; symbol_generation = self.symbol_epoch.get(watch.symbol, 0)
        history = await self.fetch_span(watch.symbol, '1h', watch.bars[-1].open_time, cutoff)
        # REST endTime selects bars, but does not truncate the current bar's OHLC.
        # Reconstruct the partial hour and minute from completed seconds instead.
        if history[-1].close_time > cutoff:
            minutes = await self.fetch_span(watch.symbol, '1m', history[-1].open_time, cutoff)
            if minutes[-1].close_time > cutoff:
                seconds = await self.fetch_span(watch.symbol, '1s', minutes[-1].open_time, cutoff)
                m = minutes[-1]
                minutes[-1] = replace(m, high=max(b.high for b in seconds), low=min(b.low for b in seconds),
                                      close=seconds[-1].close, volume=sum(b.volume for b in seconds))
            h = history[-1]
            history[-1] = replace(h, high=max(b.high for b in minutes), low=min(b.low for b in minutes),
                                 close=minutes[-1].close, volume=sum(b.volume for b in minutes))
        if not math.isclose(history[0].open, watch.plan.candle3_open, rel_tol=1e-9):
            raise ValueError('History open mismatch')
        # Existing chronological refinement has well-tested gap/ambiguity checks.
        adapted = infra.TouchPlan(watch.plan.direction, watch.plan.contact,
                                  watch.plan.upper-watch.plan.lower,
                                  watch.plan.impulse_distance, watch.plan.candle3_open)
        event, extreme = await self.walk_sequence(watch.symbol, history, adapted, cutoff)
        if generation != self.stream_epoch or symbol_generation != self.symbol_epoch.get(watch.symbol, 0) or not self.stream_connected:
            raise ValueError('Stream generation changed during verification')
        if event and event['reason'] != 'TOUCH_FIRST':
            watch.failed = event['reason']; return
        # Rebuild OHLC through the verified cutoff; snapshots may include a newer tail.
        b = watch.bars[-1]
        watch.bars[-1] = replace(b, high=max(c.high for c in history), low=min(c.low for c in history),
                                close=history[-1].close)
        watch.extreme = watch.bars[-1].low if watch.plan.direction == 'BULLISH' else watch.bars[-1].high
        watch.last_ms = cutoff; watch.last_price = history[-1].close
        if event:
            watch.touch_ms = event['touch_time']
            watch.post_high, watch.post_low = event['post_high'], event['post_low']
            moved = (watch.post_high-watch.plan.candle3_open if watch.plan.direction == 'BULLISH'
                     else watch.plan.candle3_open-watch.post_low)
            if moved >= watch.plan.impulse_distance:
                watch.failed = 'IMPULSE_ALREADY_AFTER_TOUCH'; return
        watch.verified_ms = cutoff; watch.stream_epoch = generation
        buffered = list(self.buffers.get(watch.symbol, ()))
        # A bounded buffer must cover the verification interval without truncation.
        if len(buffered) == 10000 and buffered[0][0] > cutoff:
            raise ValueError('Live recovery buffer overflow')
        for timestamp, price in buffered:
            if timestamp > cutoff and timestamp <= b.close_time:
                watch.observe(price, timestamp)
        # Historical closes alone never establish a fresh live hold.

    async def verify_guarded(self, watch):
        try:
            await self.verify_watch(watch)
            if watch.failed and self.watches.get(watch.key) is watch:
                self.reject(watch, watch.failed)
        except Exception as exc:
            watch.verified_ms = 0; self.stats['verification_retry'] += 1
            self.verify_retry_at[watch.key] = time.monotonic()+30
            logging.warning('TTW live verification deferred %s cause=%s', watch.key, type(exc).__name__)
        finally:
            watch.busy = False

    async def scan_symbol(self, symbol):
        try:
            bases = await self.fetch_symbol_bases(symbol)
            now = int(time.time()*1000)+self.server_offset_ms
            tick = self.tick_sizes.get(symbol)
            if not tick: return
            for tf in TIMEFRAMES:
                bars = self.candles_for_timeframe(bases, tf)
                if len(bars) < 3 or not bars[-1].open_time <= now <= bars[-1].close_time: continue
                await self.close_updates(symbol, tf, bars, now)
                for key, record in list(self.store.data['alerts'].items()):
                    s = record['setup']
                    if (s.get('strategy_version') in COMPATIBLE_VERSIONS and record['status'] == 'sent'
                            and s['symbol'] == symbol and s['timeframe'] == tf
                            and s['candle3_open_time'] == bars[-1].open_time):
                        if key not in self.watches:
                            plan = make_plan(bars[-3:], s['direction'], tick,
                                             s['geometry_fraction'], s['geometry_price_cap_pct'])
                            if plan:
                                restored = Watch(key, symbol, tf, bars[-8:], plan)
                                restored.alerted = True; restored.verified_ms = now
                                restored.extreme = bars[-1].low if s['direction'] == 'BULLISH' else bars[-1].high
                                self.watches[key] = restored
                        elif self.watches[key].alerted:
                            w = self.watches[key]; tail = w.bars[-1]; incoming = bars[-1]
                            w.bars[-1] = replace(tail, high=max(tail.high, incoming.high), low=min(tail.low, incoming.low))
                for direction in ('BULLISH', 'BEARISH'):
                    key = f'{symbol}|{tf}|{direction}|{bars[-1].open_time}'
                    if key in self.store.data['alerts'] or key in self.watches: continue
                    rejection = self.store.data['sequence_rejections'].get(key, {})
                    if rejection.get('strategy_version') == VERSION: continue
                    self.stats['evaluated'] += 1
                    context = reversal_context(bars, direction, tick)
                    slot = (symbol, tf, direction)
                    decision = (key, context['reason'])
                    if self.context_log.get(slot) != decision:
                        self.context_log[slot] = decision
                        logging.info('TTW_CONTEXT %s %s', key, json.dumps(context, allow_nan=False))
                    if not context['ok']:
                        self.stats[context['reason']] += 1
                        continue
                    plan = make_plan(bars[-3:], direction, tick, self.cfg.geometry_wick_fraction,
                                     self.cfg.geometry_price_cap_pct, self.cfg.impulse_range_fraction,
                                     self.cfg.rejection_wick_fraction, self.cfg.max_open_gap_range)
                    if not plan:
                        self.stats['anchor_or_open_rejected'] += 1; continue
                    self.stats['anchor_candidates'] += 1
                    w = Watch(key, symbol, tf, bars[-getattr(self.cfg, 'context_history', 8):], plan)
                    parent = PARENTS.get(tf)
                    w.parent_bars = self.candles_for_timeframe(bases, parent) if parent else []
                    tip = bars[-1].low if direction == 'BULLISH' else bars[-1].high
                    if tip < plan.lower if direction == 'BULLISH' else tip > plan.upper:
                        self.reject(w, 'GEOMETRY_ZONE_EXCEEDED'); continue
                    if not self.stream_connected: continue
                    near = tip <= plan.upper if direction == 'BULLISH' else tip >= plan.lower
                    if not near:
                        self.watches[key] = w
                        self.stats['armed_before_touch'] += 1
                        continue
                    try:
                        # Keep a deferred candidate so stream events can retry it.
                        self.watches[key] = w
                        w.busy = True
                        await self.verify_watch(w)
                    except Exception as exc:
                        w.verified_ms = 0
                        self.verify_retry_at[key] = time.monotonic()+30
                        self.stats['verification_retry'] += 1
                        logging.warning('TTW history verification deferred %s cause=%s', key, type(exc).__name__); continue
                    finally:
                        w.busy = False
                    if w.failed:
                        self.reject(w, w.failed); continue
                    self.watches[key] = w
                    logging.info('TTW_WATCH %s zone=%s..%s touch=%s', key,
                                 infra.fmt_price(plan.lower), infra.fmt_price(plan.upper), w.touch_ms)
        except Exception:
            self.stats['scan_errors'] += 1; logging.exception('V3 scan failed %s', symbol)

    async def edit_record(self, key, record, status):
        text, keyboard = alert_payload(record['setup'], key, status)
        photo = record.get('delivery_kind') == 'photo'
        deliveries = record.get('deliveries')
        if deliveries is None:
            deliveries = {str(self.chat_id): {'message_id': record['message_id']}}
        async def edit(chat, delivery):
            if chat not in self.recipient_ids() or delivery.get('edited_status') == status:
                return
            payload = dict(chat_id=chat, message_id=delivery['message_id'], reply_markup=keyboard)
            payload['caption' if photo else 'text'] = text
            await self.telegram_call('editMessageCaption' if photo else 'editMessageText', payload)
            delivery['edited_status'] = status
            self.store.save()
        results = await asyncio.gather(*(edit(c, d) for c, d in deliveries.items()), return_exceptions=True)
        if any(isinstance(r, Exception) for r in results):
            raise RuntimeError('One or more recipient edits failed')

    async def close_updates(self, symbol, timeframe, bars, now):
        for key, record in self.store.data['shadow_entries'].items():
            s = record['setup']
            if (s['symbol'] != symbol or s['timeframe'] != timeframe or
                    s['candle3_close_time'] >= now or record.get('close_verdict')):
                continue
            index = next((i for i, b in enumerate(bars) if b.open_time == s['candle3_open_time']), -1)
            if index < 2: continue
            final = bars[index-2:index+1]; c3 = final[-1]; bull = s['direction'] == 'BULLISH'
            stop_hit = c3.low <= s['sl1'] if bull else c3.high >= s['sl1']
            reversal = c3.close > c3.open if bull else c3.close < c3.open
            good = geometry(final, s['direction'], s['tick_size'], s['geometry_fraction'], s['geometry_price_cap_pct'])
            record['close_verdict'] = 'CONFIRMED' if not stop_hit and reversal and good else 'INVALID'
            audit = dict(key=key, hypothetical=True, baseline=record['baseline'],
                         first_quality_reason=record['first_quality']['reason'],
                         close_verdict=record['close_verdict'], closed_candle3=asdict(c3))
            self.store.append('shadow-verdicts-v33.jsonl', audit)
            logging.info('TTW_SHADOW_CLOSE_V33 %s', json.dumps(audit, allow_nan=False))
            self.store.save()
        for key, record in list(self.store.data['alerts'].items()):
            s = record['setup']
            if s.get('strategy_version') not in COMPATIBLE_VERSIONS or s['symbol'] != symbol or s['timeframe'] != timeframe:
                continue
            if record['status'] != 'sent' or record.get('close_update_sent') or s['candle3_close_time'] >= now:
                continue
            if record.get('close_update_attempts', 0) >= 3: continue
            index = next((i for i, b in enumerate(bars) if b.open_time == s['candle3_open_time']), -1)
            if index < 2: continue
            final = bars[index-2:index+1]; c3 = final[-1]; bull = s['direction'] == 'BULLISH'
            stop_hit = c3.low <= s['sl1'] if bull else c3.high >= s['sl1']
            reversal = c3.close > c3.open if bull else c3.close < c3.open
            good = geometry(final, s['direction'], s['tick_size'], s['geometry_fraction'], s['geometry_price_cap_pct'])
            confirmed = not stop_hit and reversal and bool(good) and not record.get('live_invalidated')
            record['close_verdict'] = 'CONFIRMED' if confirmed else 'INVALID'
            record['close_reasons'] = ([] if confirmed else
                (['SL1_BREACHED'] if stop_hit else [])+
                (['WRONG_COLOUR'] if not reversal else [])+
                (['GEOMETRY_FAILED'] if not good else []))
            record['closed_candle3'] = asdict(c3)
            high = record.get('post_alert_high', s['entry']); low = record.get('post_alert_low', s['entry'])
            favourable = (high-s['entry'] if bull else s['entry']-low)/s['entry']*100
            adverse = (s['entry']-low if bull else high-s['entry'])/s['entry']*100
            record['observed_post_alert_favourable_pct'] = max(0, favourable)
            record['observed_post_alert_adverse_pct'] = max(0, adverse)
            record['close_update_attempts'] = record.get('close_update_attempts', 0)+1
            self.store.save()
            close_audit = dict(key=key, strategy_version=s['strategy_version'],
                               close_verdict=record['close_verdict'], reasons=record['close_reasons'],
                               closed_candle3=record['closed_candle3'],
                               favourable_pct=record['observed_post_alert_favourable_pct'],
                               adverse_pct=record['observed_post_alert_adverse_pct'])
            self.store.append('verdicts-v33.jsonl', close_audit)
            logging.info('TTW_CLOSE %s', json.dumps(close_audit, allow_nan=False))
            try:
                await self.edit_record(key, record, outcomes.status_text(record) if record.get('trade_tracking') else record['close_verdict'])
                record['close_update_sent'] = True; self.store.save()
            except Exception: logging.warning('Close verdict edit deferred %s', key)

    async def send_watch(self, w):
        if self.market_io.hard_cooling or self.market_recovering: return
        context = reversal_context(w.bars, w.plan.direction, w.plan.tick)
        if not context['ok']:
            self.reject(w, context['reason']); return
        setup = make_setup(w, self.cfg.stop_buffer_pct, getattr(self.cfg, 'quality_enabled', False))
        now = int(time.time()*1000)+self.server_offset_ms
        quality = self.quality_check(w, setup, now)
        if w.ready(now, self.cfg.max_entry_move_fraction):
            self.record_shadow(w, quality)
        if not quality['ok']:
            return
        snapshot = dict(alert_id=infra.alert_id(w.key), setup=setup,
                        observed_at_ms=w.last_ms, market='BINANCE_SPOT', price_scale='LOG',
                        candles=[asdict(b) for b in w.bars],
                        sequence_evidence=dict(touch_ms=w.touch_ms, rejection_since=w.reject_since,
                                               verified_prefix_ms=w.verified_ms, source='aggTrade'))
        snapshot.update(post_alert_high=setup['entry'], post_alert_low=setup['entry'],
                        reversal_context=context, entry_quality=quality)
        text, keyboard = alert_payload(setup, w.key)
        png = None
        if self.cfg.send_charts:
            try: png = await asyncio.to_thread(render_chart, w.bars, setup)
            except Exception: logging.exception('V3 chart rendering failed')
        # Rendering must not turn a fresh trigger into a stale/invalid delivery.
        now = int(time.time()*1000)+self.server_offset_ms
        if self.market_io.hard_cooling or self.market_recovering or not self.stream_connected or w.stream_epoch != self.stream_epoch or not w.ready(
                now, self.cfg.max_entry_move_fraction):
            self.stats['changed_during_render'] += 1; return
        bull = w.plan.direction == 'BULLISH'
        if (w.last_price <= setup['sl1'] if bull else w.last_price >= setup['sl1']): return
        if (w.bars[-1].low <= setup['sl1'] if bull else w.bars[-1].high >= setup['sl1']):
            self.stats['stop_changed_during_render'] += 1; return
        if abs(w.last_price-setup['entry']) > w.plan.tick*2:
            self.stats['changed_during_render'] += 1; return
        if not self.quality_check(w, dict(setup, entry=w.last_price), now)['ok']:
            return
        if getattr(self.cfg, 'quality_enabled', False):
            # Trades observed during rendering are covered by the refreshed
            # extrema/stop checks above, not treated as a missing sequence.
            snapshot['trade_tracking'] = outcomes.start_tracking(setup, w.last_ms, self.symbol_last_id.get(w.symbol))
        snapshot['delivery_kind'] = 'photo' if png else 'text'
        self.store.reserve(w.key, snapshot); self.store.append('alerts-v3.jsonl', snapshot)
        if snapshot.get('trade_tracking'):
            self.outcome_keys.setdefault(w.symbol, set()).add(w.key)
        w.alerted = True
        logging.info('TTW_AUDIT_V3 %s', json.dumps(dict(snapshot, key=w.key, candles=snapshot['candles'][-8:]), allow_nan=False))
        await self.broadcast_alert(w.key, text, keyboard, png)

    async def broadcast_alert(self, key, text, keyboard, png=None):
        record = self.store.data['alerts'][key]
        record['deliveries'] = {}
        self.store.save()
        async def send(chat):
            try:
                if png:
                    result = await self.telegram_photo(dict(chat_id=chat, caption=text, reply_markup=keyboard), png)
                else:
                    result = await self.telegram_call('sendMessage', dict(chat_id=chat, text=text, reply_markup=keyboard))
                message_id = result['result']['message_id']
                record['deliveries'][chat] = {'message_id': message_id}
                # Retain the owner's message ID for legacy readers where possible.
                primary = record['deliveries'].get(str(self.chat_id), {'message_id': message_id})
                self.store.sent(key, primary['message_id'])
            except Exception:
                logging.error('Delivery uncertain %s recipient=%s; no automatic resend', key, chat)
        await asyncio.gather(*(send(chat) for chat in self.recipient_ids()))
        if record['deliveries']:
            self.stats['sent'] += 1
            logging.info('TTW_SENT %s recipients=%d', key, len(record['deliveries']))

    async def update_live_records(self, now):
        for key, r in list(self.store.data['alerts'].items()):
            s = r['setup']
            if s.get('strategy_version') not in COMPATIBLE_VERSIONS or r['status'] != 'sent' or r.get('live_invalidation_sent'):
                continue
            if r.get('trade_tracking'):
                continue
            if now > s['candle3_close_time']: continue
            w = self.watches.get(key)
            if not w: continue
            # Immutable C3 stop and alert-time geometry: no widening after entry.
            bull = s['direction'] == 'BULLISH'; c3 = w.bars[-1]
            stop_hit = c3.low <= s['sl1'] if bull else c3.high >= s['sl1']
            if not stop_hit: continue
            r['live_invalidated'] = True; self.store.save()
            if r.get('live_invalidation_attempts', 0) >= 3: continue
            r['live_invalidation_attempts'] = r.get('live_invalidation_attempts', 0)+1
            try:
                await self.edit_record(key, r, 'INVALID'); r['live_invalidation_sent'] = True
            except Exception: logging.warning('Live stop edit deferred %s', key)
            self.store.save()

    async def trigger_loop(self):
        while not self.stop_event.is_set():
            now = int(time.time()*1000)+self.server_offset_ms
            for w in list(self.watches.values()):
                if now > w.bars[-1].close_time:
                    self.watches.pop(w.key, None); continue
                if w.failed and not w.alerted:
                    self.reject(w, w.failed); continue
                if not self.market_io.hard_cooling and not self.market_recovering and self.stream_connected and w.stream_epoch == self.stream_epoch and w.ready(
                        now, self.cfg.max_entry_move_fraction):
                    if not w.busy and len(self.pending_sends) < 2:
                        w.busy = True
                        task = asyncio.create_task(self.send_guarded(w))
                        self.pending_sends.add(task)
                        task.add_done_callback(self.pending_sends.discard)
                if w.failed and not w.alerted:
                    self.reject(w, w.failed)
            await self.update_live_records(now)
            if now-self.last_outcome_flush >= 5000:
                await self.flush_outcomes(now)
                self.last_outcome_flush = now
            await asyncio.sleep(.25)

    async def send_guarded(self, watch):
        try: await self.send_watch(watch)
        except Exception: logging.exception('V3 alert preparation failed %s', watch.key)
        finally: watch.busy = False

    async def scanner_loop(self):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if not self.market_io.cooling and (not self.last_clock_sync or self.market_recovering or time.monotonic()-self.last_clock_sync >= 300):
                    clock_start = int(time.time()*1000)
                    clock = await self._get_json(f'{infra.BINANCE_API}/api/v3/time')
                    self.server_offset_ms = int(clock['serverTime'])-(clock_start+int(time.time()*1000))//2
                    self.last_clock_sync = time.monotonic()
                    self.market_recovering = False
                if self.market_io.hard_cooling or self.market_recovering:
                    raise MarketCooldown('Exchange recovery pending')
                if time.monotonic()-self.last_clock_sync > 600:
                    self.market_recovering = True
                    raise MarketCooldown('Clock recovery pending')
                if not self.market_io.cooling and time.time()-self.last_universe_refresh >= self.cfg.universe_refresh:
                    try: await self.refresh_universe()
                    except MarketCooldown:
                        if self.market_io.hard_cooling: raise
                for i in range(0, len(self.symbols), 5):
                    await asyncio.gather(*(self.scan_symbol(s) for s in self.symbols[i:i+5]))
                expired = [k for k,v in self.store.data['sequence_rejections'].items()
                           if v['close_time'] < int(time.time()*1000)-86400000]
                for k in expired: del self.store.data['sequence_rejections'][k]
                self.verify_retry_at = {k:v for k,v in self.verify_retry_at.items() if k in self.watches}
                if time.time()-self.last_summary >= 60:
                    logging.info('TTW_HEARTBEAT version=%s stream=%s watches=%d cycle=%.2fs stats=%s',
                                 VERSION, self.stream_connected, len(self.watches), time.monotonic()-started, dict(self.stats))
                    logging.info('TTW_MARKET_IO %s',self.market_io.status())
                    self.last_summary = time.time()
                self.store.save()
            except MarketCooldown: pass
            except Exception: logging.exception('V3 scanner cycle failed')
            if time.time()-self.last_summary >= 60:
                logging.info('TTW_HEALTH version=%s stream=%s watches=%s market=%s',
                             VERSION,self.stream_connected,len(self.watches),self.market_io.status())
                self.last_summary=time.time()
            await asyncio.sleep(max(1, self.cfg.scan_interval-(time.monotonic()-started)))

    async def run(self):
        await self.telegram_call('deleteWebhook', {'drop_pending_updates': False})
        await self.telegram_call('getMe', {})
        while not self.stop_event.is_set():
            try:
                await self.refresh_universe()
                break
            except MarketCooldown:
                logging.info('TTW_HEALTH startup paused market=%s',self.market_io.status())
                try: await asyncio.wait_for(self.stop_event.wait(),timeout=30)
                except asyncio.TimeoutError: pass
        if self.stop_event.is_set(): return
        logging.info('TTW %s online: log geometry, broader reversal or C1/C2 pullback, conditional C1 colour, slope-aware C3, live reversal-colour trigger',
                     VERSION)
        tasks = [asyncio.create_task(self.telegram_poll()), asyncio.create_task(self.scanner_loop()),
                 asyncio.create_task(self.stream_loop()), asyncio.create_task(self.trigger_loop())]
        if getattr(self.cfg, 'quality_enabled', False):
            tasks.append(asyncio.create_task(self.context_loop()))
        stopping = asyncio.create_task(self.stop_event.wait())
        try:
            done, _ = await asyncio.wait(tasks+[stopping], return_when=asyncio.FIRST_COMPLETED)
            if stopping not in done:
                for task in done:
                    task.result()
                raise RuntimeError('A scanner worker stopped unexpectedly')
        finally:
            shutdown = tasks + [stopping] + list(self.pending_sends) + list(self.verification_jobs)
            for task in shutdown: task.cancel()
            await asyncio.gather(*shutdown, return_exceptions=True)


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    scanner = Scanner(Config())
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, scanner.stop_event.set)
    await scanner.run()


