"""Chronological target-before-stop accounting, separate from C3 confirmation.

Unobserved intervals never become manufactured wins. An observed event after a
gap is UNKNOWN for an unresolved path. Fees are not estimated as actual P&L.
"""
from copy import deepcopy
from collections import Counter, defaultdict
from TTW_BOT_V2 import validate_series


def start_tracking(setup, observed_ms, trade_id=None, horizon_bars=3):
    duration = setup['candle3_close_time']-setup['candle3_open_time']+1
    return dict(origin_ms=observed_ms, last_ms=observed_ms, last_trade_id=trade_id,
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


def recovery_cursor(record):
    tracking = record.get('trade_tracking')
    if not tracking:
        return None
    positions = tracking.get('recovery_positions', {})
    cursors = [positions.get(stop+':'+target, tracking['last_ms']+1)
               for stop, targets in tracking['paths'].items()
               for target, state in targets.items() if state == 'UNKNOWN']
    return min(cursors) if cursors else None


def recover_ranges(record, bars, now_ms):
    """Resolve UNKNOWN paths only from complete, contiguous candle evidence.

    Start at each path's last verified cursor and exclude pre-alert barrier
    contact. A candle containing both barriers remains ambiguous even when
    later candles look favourable. Existing observed outcomes are immutable.
    No event timestamp or within-candle price path is invented.
    """
    tracking = record.get('trade_tracking')
    if not tracking or not any(v == 'UNKNOWN' for p in tracking['paths'].values() for v in p.values()):
        return False
    s = record['setup']; origin = tracking.get('origin_ms', record.get('observed_at_ms'))
    if origin is None:
        return False
    rows = [b for b in bars if b.close_time < now_ms and b.close_time <= tracking['deadline_ms']]
    if not rows or validate_series(rows) != rows:
        return False
    prefix = next((b for b in record.get('candles', []) if b['open_time'] == s['candle3_open_time']), None)
    bull = s['direction'] == 'BULLISH'
    changed = False
    positions = tracking.setdefault('recovery_positions', {})
    for stop, targets in tracking['paths'].items():
        for target, state in list(targets.items()):
            if state != 'UNKNOWN':
                continue
            path = stop+':'+target
            cursor = positions.get(path, tracking['last_ms']+1)
            following = [b for b in rows if b.close_time >= cursor]
            if not following or not following[0].open_time <= cursor <= following[0].close_time:
                continue
            if following[0].open_time < origin:
                if prefix is None or following[0].open_time < prefix['open_time']:
                    continue
                prefix_stop = prefix['low'] <= s[stop] if bull else prefix['high'] >= s[stop]
                prefix_target = prefix['high'] >= s[target] if bull else prefix['low'] <= s[target]
                if prefix_stop or prefix_target:
                    continue
                if not following[0].low <= prefix['close'] <= following[0].high:
                    continue
            result = None
            for bar in following:
                hit_stop = bar.low <= s[stop] if bull else bar.high >= s[stop]
                hit_target = bar.high >= s[target] if bull else bar.low <= s[target]
                if hit_stop and hit_target:
                    break
                if hit_stop or hit_target:
                    result = 'STOP' if hit_stop else 'TP'
                    break
                positions[path] = bar.close_time+1
                changed = True
            else:
                if following[-1].close_time == tracking['deadline_ms']:
                    result = 'EXPIRED'; bar = following[-1]
            if result is None:
                continue
            targets[target] = result
            proof = dict(stop=stop, target=target, result=result,
                         method='COMPLETE_CANDLE_RANGES', previous='UNKNOWN',
                         interval_start_ms=max(bar.open_time, cursor),
                         interval_end_ms=bar.close_time, recovered_at_ms=now_ms)
            if result == 'EXPIRED':
                proof['deadline_close_price'] = bar.close
            tracking.setdefault('recoveries', []).append(proof)
            changed = True
    if changed:
        tracking['revision'] += 1
    return changed


def performance_text(records, version=None):
    """Report delivered signals by feed and horizon; gaps stay in the totals."""
    groups = defaultdict(lambda: {'sl1': Counter(), 'sl2': Counter()})
    untracked = 0
    for record in records:
        s = record.get('setup', {})
        if record.get('status') != 'sent' or (version and s.get('strategy_version') != version):
            continue
        t = record.get('trade_tracking')
        if not t:
            untracked += 1; continue
        tf = s.get('timeframe', '')
        horizon = 'Intraday' if tf.endswith('H') else 'Daily' if tf.endswith('D') else 'Weekly/monthly'
        market = 'Perps' if record.get('market', s.get('market')) == 'BINANCE_FUTURES' else 'Spot'
        for stop in ('sl1', 'sl2'):
            groups[(market, horizon)][stop][t['paths'][stop]['tp1']] += 1
    lines = ['TP1 target-before-stop · '+(version or 'all saved versions')]
    for (market, horizon), paths in sorted(groups.items()):
        lines.append(market+' · '+horizon)
        for stop, c in paths.items():
            decided = c['TP']+c['STOP']
            rate = f'{c["TP"]/decided:.0%}' if decided else 'n/a'
            lines.append(f'{stop.upper()}: TP {c["TP"]}, stop {c["STOP"]}, unknown {c["UNKNOWN"]}, expired {c["EXPIRED"]}, open {c["OPEN"]} · decided {rate}')
    if not groups:
        lines.append('No tracked delivered signals in this selection.')
    if untracked:
        lines.append(f'Older untracked signals excluded: {untracked}')
    lines.append('Decided % excludes unknown/expired/open. Signal paths, not realised P&L.')
    return '\n'.join(lines)


def summary(record):
    s = record['setup']; tracking = record['trade_tracking']
    paths = {}
    for stop, targets in tracking['paths'].items():
        risk = abs(s['entry']-s[stop])
        paths[stop] = dict(targets, risk_pct=risk/s['entry']*100,
                          target1_r=abs(s['tp1']-s['entry'])/risk if risk else None)
    return dict(paths=paths, deadline_ms=tracking['deadline_ms'], gap=tracking['gap'],
                gap_reason=tracking.get('gap_reason'), gap_ms=tracking.get('gap_ms'),
                recovery_positions=deepcopy(tracking.get('recovery_positions', {})),
                events=deepcopy(tracking.get('events', [])),
                recoveries=deepcopy(tracking.get('recoveries', [])), revision=tracking['revision'])


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
