"""Causal lesson evidence, independent of entry and target/stop outcomes.

The numerical strength thresholds below are explicit first-pass hypotheses.
They classify observations; they do not add an entry veto or a win probability.
Only records stamped with this policy receive the new close interpretation.
"""
from copy import deepcopy
import math

POLICY = 'MENTOR_LESSON_20261103_V1'
WEAK_BODY_FRACTION = .25
MIN_RECOVERY_FRACTION = .50
MAX_CANDIDATES = 512


def _valid(bars):
    return bool(bars) and all(
        all(math.isfinite(x) and x > 0 for x in (b.open, b.high, b.low, b.close))
        and b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        and b.open_time <= b.close_time for b in bars) and all(
        a.close_time+1 == b.open_time for a, b in zip(bars, bars[1:]))


def exhaustion(bars, direction, tick, *, closed=False):
    """Weak prior-direction C3 and strong opposing C3 are distinct routes."""
    result = dict(policy=POLICY, state='UNRESOLVED', supports_reversal=False,
                  closed=closed, thresholds=dict(weak_body_fraction=WEAK_BODY_FRACTION,
                                                min_recovery_fraction=MIN_RECOVERY_FRACTION))
    if (direction not in ('BULLISH', 'BEARISH') or len(bars) < 3
            or not math.isfinite(tick) or tick <= 0 or not _valid(bars[-3:])):
        return dict(result, reason='INVALID_CANDLE_EVIDENCE')
    c1, c2, c3 = bars[-3:]
    bull = direction == 'BULLISH'; sign = 1 if bull else -1
    span = c3.high-c3.low
    if span < 2*tick:
        return dict(result, reason='INSUFFICIENT_RANGE')
    body = abs(c3.close-c3.open)
    wick = min(c3.open, c3.close)-c3.low if bull else c3.high-max(c3.open, c3.close)
    recovery = c3.close-c3.low if bull else c3.high-c3.close
    reversal_body = sign*(c3.close-c3.open)
    body_fraction = body/span; recovery_fraction = recovery/span
    visible = wick >= 2*tick-tick*1e-8
    recovered = recovery_fraction >= MIN_RECOVERY_FRACTION
    opposing = reversal_body >= tick*(1-1e-8) and recovered and visible
    weak = (reversal_body < tick and body_fraction <= WEAK_BODY_FRACTION
            and recovered and visible)
    state = ('REVERSAL_PRESSURE' if opposing else 'WEAK_CONTINUATION' if weak else
             'CONTINUATION_PRESSURE' if reversal_body < -tick and body_fraction > WEAK_BODY_FRACTION
             else 'UNRESOLVED')
    body_boundary = (max(max(b.open, b.close) for b in (c1, c2)) if bull else
                     min(min(b.open, b.close) for b in (c1, c2)))
    extreme_boundary = max(c1.high, c2.high) if bull else min(c1.low, c2.low)
    return dict(result, state=state, supports_reversal=opposing or weak,
                reason=state, body=body, relevant_wick=wick, range=span,
                body_fraction=body_fraction, recovery_fraction=recovery_fraction,
                reversal_body=reversal_body, visible_wick=visible,
                anchor_body_boundary=body_boundary,
                anchor_body_reclaimed=sign*(c3.close-body_boundary) >= tick*(1-1e-8),
                anchor_extreme=extreme_boundary,
                anchor_extreme_broken=sign*(c3.close-extreme_boundary) >= tick*(1-1e-8))


def alignment_comparison(bars, direction, half_width=.3):
    """Report both measurements, never choose a scale to rescue a candidate."""
    if direction not in ('BULLISH', 'BEARISH') or len(bars) != 3 or not _valid(bars):
        return dict(source_scale='LOG', comparison_available=False)
    bull = direction == 'BULLISH'
    tips = [b.low if bull else b.high for b in bars]
    linear = (tips[0]+tips[2])/2
    logarithmic = math.sqrt(tips[0]*tips[2])
    body_clear = True
    for i, b in enumerate(bars):
        values = [tips[0]+(tips[2]-tips[0])*x/2
                  for x in (max(0, i-half_width), min(2, i+half_width))]
        if (max(values) >= min(b.open,b.close) if bull else
                min(values) <= max(b.open,b.close)):
            body_clear = False
    return dict(source_scale='LOG', comparison_available=True, policy='MEASUREMENT_ONLY',
                linear_expected_c2=linear, log_expected_c2=logarithmic,
                linear_gap_pct=abs(tips[1]/linear-1)*100,
                log_gap_pct=abs(tips[1]/logarithmic-1)*100,
                midpoint_difference_pct=abs(linear/logarithmic-1)*100,
                linear_body_clear=body_clear)


def level_reaction(bars, direction, level, tick, scale):
    """Describe rejection versus bodies accepting beside a known level."""
    if (len(bars) < 3 or not _valid(bars[-3:]) or direction not in ('BULLISH','BEARISH')
            or level is None or not math.isfinite(level) or scale <= 0 or tick <= 0):
        return dict(state='UNAVAILABLE', scope='OBSERVE_ONLY')
    sign = 1 if direction == 'BULLISH' else -1
    zone = max(2*tick,.1*scale)
    rows = bars[-3:]
    closes_near = [abs(b.close-level) <= zone for b in rows]
    wick_space = [min(b.open,b.close)-b.low if sign == 1 else b.high-max(b.open,b.close)
                  for b in rows]
    compressed = all(closes_near[-2:]) and all(w <= zone for w in wick_space[-2:])
    reaction = sign*(rows[-1].close-level) > zone and wick_space[-1] > zone
    return dict(state='COMPRESSION' if compressed else 'REACTION' if reaction else 'MIXED',
                scope='OBSERVE_ONLY', level=level, zone=zone,
                closes_near_level=closes_near, relevant_wicks=wick_space)


def close_classification(bars, setup, geometry_ok):
    evidence = exhaustion(bars,setup['direction'],setup['tick_size'],closed=True)
    verdict = ('INVALID' if not geometry_ok else 'CONFIRMED' if evidence['supports_reversal']
               else 'INVALID' if evidence['state'] == 'CONTINUATION_PRESSURE' else 'UNCONFIRMED')
    reasons = (['GEOMETRY_FAILED'] if not geometry_ok else [] if verdict == 'CONFIRMED'
               else [evidence['reason']])
    return dict(policy=POLICY, verdict=verdict, reasons=reasons, exhaustion=evidence,
                trade_outcome_separate=True)


def follow_through(c3, c4, direction, tick, now_ms):
    if (direction not in ('BULLISH','BEARISH') or not _valid([c3,c4])
            or c4.close_time >= now_ms or tick <= 0):
        return None
    sign = 1 if direction == 'BULLISH' else -1
    boundary = c3.high if sign == 1 else c3.low
    extended = sign*(c4.close-boundary) >= tick*(1-1e-8)
    progressed = sign*(c4.close-c3.close) >= tick*(1-1e-8)
    return dict(policy=POLICY, status='EXTENDED' if extended else 'PROGRESSED' if progressed else 'STALLED',
                known_at_ms=c4.close_time+1, c4_open_time=c4.open_time,
                c4_close=c4.close, c3_extreme=boundary, c3_close=c3.close,
                scope='OBSERVED_FOLLOW_THROUGH', changes_original_trade=False)


def candidate_id(market, symbol, timeframe, direction, opened):
    return f'{market}|{symbol}|{timeframe}|{direction}|{opened}'


def retain_candidate(rows, key, candidate, limit=MAX_CANDIDATES):
    """Bound shared persistent state; preserve the actual first observation."""
    prior = rows.get(key)
    candidate = deepcopy(candidate)
    candidate['first_seen_ms'] = prior['first_seen_ms'] if prior else candidate['observed_at_ms']
    candidate['observed_before_close'] = (prior.get('observed_before_close', False) if prior else
                                         candidate['observed_at_ms'] <= candidate['close_ms'])
    rows[key] = candidate
    if len(rows) > limit:
        ordered = sorted(rows,key=lambda k:(rows[k].get('observed_at_ms',0),k))
        for old in ordered[:len(rows)-limit]:
            del rows[old]
    return prior is None or prior.get('phase') != candidate.get('phase')


def parent_context(rows, market, symbol, duration_ms, direction, now_ms, price, *, timeframe=None):
    eligible=[]
    for key,row in rows.items():
        if (row.get('market') != market or row.get('symbol') != symbol
                or row.get('timeframe') == timeframe
                or row.get('kind') != 'TTW' or row.get('duration_ms',0) <= duration_ms
                or row.get('observed_at_ms',now_ms+1) > now_ms
                or row.get('expires_ms',0) < now_ms
                or row.get('context_invalidated')
                or row.get('context_gap')
                or not row.get('exhaustion',{}).get('supports_reversal')
                or row.get('verdict') in ('INVALID','UNCONFIRMED')):
            continue
        boundary=row.get('invalidation')
        if boundary is None or (price <= boundary if row['direction']=='BULLISH' else price >= boundary):
            continue
        eligible.append(dict(pattern_id=key,timeframe=row['timeframe'],direction=row['direction'],
                             invalidation=boundary,duration_ms=row['duration_ms'],
                             phase=row['phase'],known_at_ms=row['first_seen_ms'],
                             relation='ALIGNED' if row['direction']==direction else 'COUNTERTREND'))
    eligible.sort(key=lambda r:(r['duration_ms'],r['pattern_id']))
    selected = [eligible[0]] if eligible else []
    if eligible and eligible[-1]['pattern_id'] != eligible[0]['pattern_id']:
        selected.append(eligible[-1])
    return dict(available=bool(selected),parents=selected,scope='CONTEXT_ONLY',entry_veto=False)


def candidate_summary(rows, market):
    selected=[r for r in rows.values() if r.get('market')==market]
    return dict(retained=len(selected),live=sum(r.get('phase')=='LIVE' for r in selected),
                closed=sum(r.get('phase')=='CLOSED' for r in selected),
                confirmed=sum(r.get('verdict')=='CONFIRMED' for r in selected),
                weak_colour=sum(r.get('exhaustion',{}).get('state')=='WEAK_CONTINUATION' for r in selected),
                early_entry_policy='LIVE_COLOUR_FLIP',exhaustion_entry_veto=False)
