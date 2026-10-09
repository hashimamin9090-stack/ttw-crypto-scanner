"""Chronological target-before-stop accounting, separate from C3 confirmation.

Unobserved intervals never become manufactured wins. An observed event after a
gap is UNKNOWN for an unresolved path. Fees are not estimated as actual P&L.
"""
from copy import deepcopy


def start_tracking(setup, observed_ms, trade_id=None, horizon_bars=3):
    duration = setup['candle3_close_time']-setup['candle3_open_time']+1
    return dict(last_ms=observed_ms, last_trade_id=trade_id,
                deadline_ms=setup['candle3_open_time']+horizon_bars*duration-1,
                paths={name: {'tp1': 'OPEN', 'tp2': 'OPEN'} for name in ('sl1', 'sl2')},
                revision=0, gap=False)


def open_paths(tracking):
    return any(state == 'OPEN' for targets in tracking['paths'].values() for state in targets.values())


def observe(record, price, timestamp, trade_id):
    tracking = record.get('trade_tracking')
    if not tracking or not open_paths(tracking):
        return False
    if timestamp < tracking['last_ms']:
        return False
    previous = tracking.get('last_trade_id')
    if previous is not None and trade_id is not None and trade_id <= previous:
        return False
    if previous is not None and trade_id is not None and trade_id != previous+1:
        return mark_gap(record, timestamp, 'TRADE_SEQUENCE_GAP')
    if timestamp > tracking['deadline_ms']:
        return expire(record, timestamp)
    tracking['last_ms'] = timestamp
    tracking['last_trade_id'] = trade_id
    s = record['setup']; bull = s['direction'] == 'BULLISH'
    changed = False
    for stop, targets in tracking['paths'].items():
        hit_stop = price <= s[stop] if bull else price >= s[stop]
        for target, state in list(targets.items()):
            if state != 'OPEN':
                continue
            hit_target = price >= s[target] if bull else price <= s[target]
            if hit_stop or hit_target:
                targets[target] = 'STOP' if hit_stop else 'TP'
                tracking.setdefault('events', []).append(dict(stop=stop, target=target,
                    result=targets[target], price=price, timestamp=timestamp))
                changed = True
    if changed:
        tracking['revision'] += 1
    return changed


def mark_gap(record, timestamp, reason='STREAM_GAP'):
    tracking = record.get('trade_tracking')
    if not tracking or not open_paths(tracking):
        return False
    for targets in tracking['paths'].values():
        for target, state in targets.items():
            if state == 'OPEN':
                targets[target] = 'UNKNOWN'
    tracking.update(gap=True, gap_reason=reason, gap_ms=timestamp,
                    revision=tracking['revision']+1)
    return True


def expire(record, now_ms):
    tracking = record.get('trade_tracking')
    if not tracking or now_ms <= tracking['deadline_ms'] or not open_paths(tracking):
        return False
    for targets in tracking['paths'].values():
        for target, state in targets.items():
            if state == 'OPEN':
                targets[target] = 'EXPIRED'
    tracking['revision'] += 1
    return True


def summary(record):
    s = record['setup']; tracking = record['trade_tracking']
    paths = {}
    for stop, targets in tracking['paths'].items():
        risk = abs(s['entry']-s[stop])
        paths[stop] = dict(targets, risk_pct=risk/s['entry']*100,
                          target1_r=abs(s['tp1']-s['entry'])/risk if risk else None)
    return dict(paths=paths, deadline_ms=tracking['deadline_ms'], gap=tracking['gap'],
                events=deepcopy(tracking.get('events', [])), revision=tracking['revision'])


def status_text(record):
    bits = []
    verdict = record.get('close_verdict')
    if verdict:
        bits.append('PATTERN '+verdict)
    for stop, targets in record['trade_tracking']['paths'].items():
        a, b = targets['tp1'], targets['tp2']
        if a == 'TP':
            bits.append(stop.upper()+(' TP1+TP2' if b == 'TP' else ' TP1'))
        elif a != 'OPEN':
            bits.append(stop.upper()+' '+('HIT' if a == 'STOP' else a))
    return ' · '.join(bits)
