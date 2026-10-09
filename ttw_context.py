"""Causal entry context for TTWs. Thresholds are initial hypotheses, not fitted odds.

Levels use completed candles that predate C1 (or C3 for the parent timeframe).
BTC expansion uses the live quote; no future swing or candle close is required.
"""
import math
from TTW_BOT_V2 import validate_series

PARENTS = {'2H': '12H', '3H': '12H', '4H': '1D', '6H': '1D',
           '12H': '1D', '1D': '1W', '2D': '1W', '3D': '1W',
           '4D': '1W', '5D': '1W', '1W': '1M', '2W': '1M'}


def atr(bars, length=14):
    if len(bars) < 2 or validate_series(bars) != list(bars):
        return 0.0
    ranges = [max(b.high-b.low, abs(b.high-a.close), abs(b.low-a.close))
              for a, b in zip(bars, bars[1:])]
    return sum(ranges[-length:])/len(ranges[-length:])


def established_levels(bars, cutoff, window=32):
    """One-bar-right pivots become usable only after that right bar has closed."""
    rows = [b for b in bars if b.close_time < cutoff][-window:]
    if len(rows) < 6 or validate_series(rows) != rows:
        return [], [], 0.0
    supports, resistances = [], []
    for a, b, c in zip(rows, rows[1:], rows[2:]):
        if b.low <= a.low and b.low < c.low:
            supports.append((b.low, 'SWING_LOW', c.close_time))
        if b.high >= a.high and b.high > c.high:
            resistances.append((b.high, 'SWING_HIGH', c.close_time))
    supports.append((min(b.low for b in rows), 'RANGE_LOW', rows[-1].close_time))
    resistances.append((max(b.high for b in rows), 'RANGE_HIGH', rows[-1].close_time))
    return supports, resistances, atr(rows)


def location_context(bars, parent_bars, direction, price, tip, tick,
                     proximity=.35):
    if direction not in ('BULLISH', 'BEARISH') or len(bars) < 3:
        return dict(ok=False, reason='LOCATION_HISTORY_MISSING')
    bull = direction == 'BULLISH'
    # C1 and C2 cannot invent their own older support/resistance.
    own = established_levels(bars[:-3], bars[-3].open_time)
    parent = established_levels(parent_bars or [], bars[-1].open_time)
    setup_atr = atr(bars[:-1])
    if setup_atr <= 0:
        return dict(ok=False, reason='LOCATION_HISTORY_MISSING')
    matches, obstacles = [], []
    for source, (supports, resistances, scale) in [('SETUP', own), ('PARENT', parent)]:
        if scale <= 0:
            continue
        allowance = max(2*tick, min(proximity*scale, .75*setup_atr))
        for level, kind, known in supports if bull else resistances:
            # The level must still be on the protective side of entry.
            protective = level < price if bull else level > price
            if protective and abs(tip-level) <= allowance:
                matches.append(dict(level=level, kind=kind, source=source,
                                    known_at_ms=known, distance=abs(tip-level),
                                    allowance=allowance))
        for level, kind, known in resistances if bull else supports:
            ahead = level > price+2*tick if bull else level < price-2*tick
            if ahead:
                obstacles.append(dict(level=level, kind=kind, source=source,
                                      known_at_ms=known, distance=abs(price-level)))
    nearest = min(matches, key=lambda x: x['distance']) if matches else None
    obstacle = min(obstacles, key=lambda x: x['distance']) if obstacles else None
    return dict(ok=bool(nearest), reason='ESTABLISHED_LEVEL' if nearest else 'NO_ESTABLISHED_LEVEL',
                level=nearest, obstacle=obstacle, setup_atr=setup_atr)


def btc_context(short_bars, hour_bars, price, quote_ms, now_ms):
    """Block acute opposing expansion, not every countertrend reversal.

    Uses 15m for the immediate shock and completed 1H bars for a context label.
    Missing/stale data is UNKNOWN, never silently interpreted as supportive.
    """
    if (not math.isfinite(price) or price <= 0 or
            not -1000 <= now_ms-quote_ms <= 3000 or len(short_bars) < 16 or
            validate_series(short_bars) != list(short_bars)):
        return dict(ok=False, regime='UNKNOWN', reason='BTC_DATA_UNAVAILABLE')
    closed = [b for b in short_bars if b.close_time < now_ms]
    if len(closed) < 15 or now_ms-closed[-1].close_time > 900000:
        return dict(ok=False, regime='UNKNOWN', reason='BTC_DATA_UNAVAILABLE')
    scale = atr(closed)
    if scale <= 0:
        return dict(ok=False, regime='UNKNOWN', reason='BTC_DATA_UNAVAILABLE')
    live = short_bars[-1]
    if not live.open_time <= now_ms <= live.close_time:
        return dict(ok=False, regime='UNKNOWN', reason='BTC_DATA_UNAVAILABLE')
    reference = closed[-8:]
    floor, ceiling = min(b.low for b in reference), max(b.high for b in reference)
    move = price-live.open
    # Continuation of a closed breakout is checked independently of the live open.
    older = closed[-10:-2]
    two_move = price-closed[-2].open
    expanding_down = ((move <= -.9*scale and price < floor) or
                      (two_move <= -1.5*scale and price < min(b.low for b in older)))
    expanding_up = ((move >= .9*scale and price > ceiling) or
                    (two_move >= 1.5*scale and price > max(b.high for b in older)))
    hours = [b for b in hour_bars if b.close_time < now_ms]
    trend = 'UNKNOWN'
    if len(hours) >= 4 and validate_series(hours[-4:]) == hours[-4:] and now_ms-hours[-1].close_time <= 3600000:
        net = hours[-1].close-hours[-4].open
        threshold = atr(hours)*.25
        trend = 'UP' if net > threshold else 'DOWN' if net < -threshold else 'RANGE'
    regime = 'DOWN_EXPANSION' if expanding_down else 'UP_EXPANSION' if expanding_up else 'ORDERLY_OR_RANGE'
    return dict(ok=True, regime=regime, reason=regime, trend=trend,
                price=price, quote_ms=quote_ms, atr15m=scale,
                range_low=floor, range_high=ceiling,
                live_move_atr=move/scale, two_bar_move_atr=two_move/scale)


def btc_allows(context, direction):
    if not context.get('ok'):
        return False
    return not (direction == 'BULLISH' and context['regime'] == 'DOWN_EXPANSION' or
                direction == 'BEARISH' and context['regime'] == 'UP_EXPANSION')


def reclaim_distance(bars, direction, tick, fraction=.08):
    c1, c2 = bars[-3:-1]
    if direction == 'BULLISH':
        wicks = [min(c.open, c.close)-c.low for c in (c1, c2)]
    else:
        wicks = [c.high-max(c.open, c.close) for c in (c1, c2)]
    true_range = max(c2.high-c2.low, abs(c2.high-c1.close), abs(c2.low-c1.close))
    # The existing eight-tick impulse floor gives a two-tick early-entry
    # window. A three-tick minimum would make that entire window impossible.
    return max(2*tick, fraction*min(wicks), .02*true_range)


def entry_quality(bars, parent_bars, setup, btc, reclaim_fraction=.08,
                  proximity=.35, min_target_r=1.5, min_room_r=1.25):
    """All temporary vetoes keep the candidate armed; only geometry kills it."""
    s = setup; bull = s['direction'] == 'BULLISH'
    needed = reclaim_distance(bars, s['direction'], s['tick_size'], reclaim_fraction)
    body = (s['entry']-bars[-1].open) * (1 if bull else -1)
    evidence = dict(min_reclaim=needed, body_reclaim=body, btc=btc)
    if body < needed-s['tick_size']*1e-8:
        return dict(ok=False, reason='RECLAIM_TOO_SMALL', **evidence)
    if not btc_allows(btc, s['direction']):
        return dict(ok=False, reason='BTC_DATA_UNAVAILABLE' if not btc.get('ok') else 'BTC_OPPOSING_EXPANSION', **evidence)
    location = location_context(bars, parent_bars, s['direction'], s['entry'],
                                s['wick3'], s['tick_size'], proximity)
    evidence['location'] = location
    if not location['ok']:
        return dict(ok=False, reason=location['reason'], **evidence)
    risk = abs(s['entry']-s['sl1'])
    if risk <= 0:
        return dict(ok=False, reason='INVALID_RISK', **evidence)
    target_r = abs(s['tp1']-s['entry'])/risk
    room_r = location['obstacle']['distance']/risk if location['obstacle'] else None
    evidence.update(target_r=target_r, room_r=room_r)
    if target_r < min_target_r:
        return dict(ok=False, reason='TARGET_TOO_SMALL_FOR_RISK', **evidence)
    if room_r is not None and room_r < min_room_r:
        return dict(ok=False, reason='OPPOSING_LEVEL_TOO_CLOSE', **evidence)
    if (s['sl2'] >= s['entry'] if bull else s['sl2'] <= s['entry']):
        return dict(ok=False, reason='C2_STOP_NOT_PROTECTIVE', **evidence)
    return dict(ok=True, reason='QUALITY_READY', **evidence)
