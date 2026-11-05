"""Causal, bounded one-minute price evidence for developing TTW entries.

Only exchange-final kline events are accepted. An event's receipt time, rather
than its historical candle start, is when its evidence becomes available.
No OHLC path is invented, and there are no additional REST requests.
"""
from collections import deque
from dataclasses import asdict, dataclass
import math

from TTW_BOT_V2 import Candle, validate_series

POLICY = 'OBSERVED_C3_TURN_V1'
MINUTE = 60000
CAPACITY = 64


@dataclass(frozen=True)
class KnownMinute:
    bar: Candle
    known_ms: int


class MinuteBook:
    def __init__(self):
        self.rows = {}
        self.generations = {}

    def clear(self, symbol=None):
        selected = set(self.rows) | set(self.generations) if symbol is None else {symbol}
        for name in selected:
            self.rows.pop(name, None)
            self.generations[name] = self.generations.get(name, 0)+1

    def accept(self, data, received_ms):
        k = data['k']
        if k['i'] != '1m' or k['x'] is not True:
            return False
        b = Candle(int(k['t']), float(k['o']), float(k['h']), float(k['l']),
                   float(k['c']), float(k['v']), int(k['T']))
        if (validate_series([b]) != [b] or b.open_time % MINUTE
                or b.close_time-b.open_time+1 != MINUTE
                or not math.isfinite(b.volume) or b.volume < 0):
            raise ValueError('Invalid closed entry minute')
        known = max(int(data['E']), int(received_ms), b.close_time+1)
        symbol = data['s']
        rows = self.rows.setdefault(symbol, deque(maxlen=CAPACITY))
        if rows:
            tail = rows[-1]
            if b.open_time < tail.bar.open_time:
                return False
            if b.open_time == tail.bar.open_time:
                if b != tail.bar:
                    self.clear(symbol)
                return False
            if b.open_time != tail.bar.close_time+1:
                self.clear(symbol)
                rows = self.rows.setdefault(symbol, deque(maxlen=CAPACITY))
        rows.append(KnownMinute(b, known))
        return True

    def available(self, symbol, now):
        rows = [r for r in self.rows.get(symbol, ())
                if r.known_ms <= now and r.bar.close_time < now]
        if not rows or now-rows[-1].bar.close_time > 2*MINUTE:
            return []
        return rows


def _packed(row):
    return dict(candle=asdict(row.bar), known_at_ms=row.known_ms)


def turn_evidence(book, symbol, direction, price, tick, touch_ms, extreme,
                  extreme_ms, now, state, *, quote_ms=None, allow_direct=True):
    """A decisive closed-minute break OR a defended pullback/live break.

    The micro boundary is frozen from the latest minute known before the
    current C3 extreme. New C3 extremes reset it; C1/C2 TTW anchors never move.
    Later closed minutes must hold a claimed break. A missing minute or stream
    generation change cannot bridge a historical proof into the new stream.
    """
    base = dict(policy=POLICY, ok=False, route='WAIT', source='SAME_FEED_FINAL_1M',
                direct_route_eligible=allow_direct,
                thresholds=dict(direct_body_fraction=.5, direct_close_recovery=.75,
                                break_ticks=2, retest_retrace_fraction=.10))
    quote_ms = now if quote_ms is None else quote_ms
    if (direction not in ('BULLISH', 'BEARISH') or not math.isfinite(price)
            or not math.isfinite(tick) or tick <= 0 or price <= 0
            or not touch_ms or not extreme_ms or extreme_ms > now
            or quote_ms > now+1000 or now-quote_ms > 3000):
        return dict(base, reason='ENTRY_TURN_NOT_OBSERVED')
    generation = book.generations.get(symbol, 0)
    identity = (generation, extreme, extreme_ms)
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
    rows = book.available(symbol, now)
    if not rows:
        return dict(base, reason='ENTRY_MINUTES_UNAVAILABLE')
    bull = direction == 'BULLISH'
    sign = 1 if bull else -1
    eps = tick*1e-8
    if any((r.bar.low < extreme-eps if bull else r.bar.high > extreme+eps)
           for r in rows if r.bar.close_time >= touch_ms):
        return dict(base, reason='ENTRY_EXTREME_NOT_RECONCILED')
    start = max(touch_ms, extreme_ms)
    # This is the last already-known opposing micro boundary, not whichever
    # earlier price happens to make the current candidate pass.
    if 'boundary' not in state:
        prior = [r for r in rows if r.known_ms <= start and r.bar.close_time < start]
        if prior:
            state['boundary'] = _packed(prior[-1])
    boundary = state.get('boundary')
    if boundary and allow_direct:
        b = boundary['candle']
        trigger = (b['high'] if bull else b['low'])+sign*2*tick
        for index, row in enumerate(rows):
            c = row.bar
            if c.close_time < start or row.known_ms < start:
                continue
            span = c.high-c.low
            body = sign*(c.close-c.open)
            recovery = (c.close-c.low if bull else c.high-c.close)
            if (span >= 2*tick and body >= 2*tick-eps
                    and body/span >= .5 and recovery/span >= .75
                    and sign*(c.close-trigger) >= -eps
                    and row.known_ms <= quote_ms
                    and sign*(price-trigger) >= -eps):
                if not all(sign*(later.bar.close-trigger) >= -eps for later in rows[index+1:]):
                    continue
                return dict(base, ok=True, reason='ENTRY_TURN_CONFIRMED', route='DIRECT_REJECTION',
                            trigger=trigger, boundary=boundary, evidence=[_packed(row)],
                            known_at_ms=row.known_ms, extreme=extreme, extreme_known_at_ms=extreme_ms)
    # A and B are completed and consecutive. A fixes the rebound extreme;
    # the whole of B defends the C3 extreme and retreats without extending A.
    # The fresh live quote can break A: no third-minute close is required.
    for index in range(len(rows)-1):
        a, b = rows[index:index+2]
        x, y = a.bar, b.bar
        if (x.close_time < start or a.known_ms < start
                or y.open_time != x.close_time+1 or b.known_ms > now):
            continue
        peak = x.high if bull else x.low
        rebound = sign*(peak-extreme)
        retrace = sign*(x.close-y.close)
        defended = y.low >= extreme+tick-eps if bull else y.high <= extreme-tick+eps
        lower_peak = y.high <= peak-tick+eps if bull else y.low >= peak+tick-eps
        trigger = peak+sign*2*tick
        if (rebound >= 4*tick-eps and sign*(x.close-extreme) >= 2*tick-eps
                and retrace >= max(2*tick, .10*rebound)-eps
                and defended and lower_peak
                and b.known_ms <= quote_ms
                and sign*(price-trigger) >= -eps):
            if not all(sign*(later.bar.close-trigger) >= -eps for later in rows[index+2:]):
                continue
            return dict(base, ok=True, reason='ENTRY_TURN_CONFIRMED', route='DEFENDED_RETEST',
                        trigger=trigger, evidence=[_packed(a), _packed(b)],
                        known_at_ms=quote_ms, extreme=extreme, extreme_known_at_ms=extreme_ms)
    return dict(base, reason='ENTRY_TURN_UNCONFIRMED', boundary=boundary,
                extreme=extreme, extreme_known_at_ms=extreme_ms)


def anchor_pressure(bars, direction, tick):
    """Completed-anchor measurements; a small partial C3 is not exhaustion."""
    sign = -1 if direction == 'BULLISH' else 1
    a, b = bars[-3:-1]
    first, second = sign*(a.close-a.open), sign*(b.close-b.open)
    return dict(c1_approach_body=first, c2_approach_body=second,
                completed_pressure_fading=first > 0 and second < first,
                c2_reversed_pressure=second < 0,
                direct_route_eligible=first >= tick*(1-1e-8) and -tick*1e-8 <= second < first-tick*1e-8,
                c3_size_comparison='NOT_USED_PARTIAL_CANDLE', scope='ENTRY_ROUTE_SELECTION')
