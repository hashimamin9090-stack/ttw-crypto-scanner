"""Bounded causal swing observations; never fabricates a third-tap trade.

Confirmed pivots use two completed right-hand candles. A live third visit
is only a watch, and a resolved move is reported when actually observed.
The prominence/zone defaults are research hypotheses, not mentor numbers.
"""
import hashlib
import math
from copy import deepcopy

POLICY='THREE_TAP_SWINGS_V1'
WINDOW=40
RIGHT_BARS=2
MIN_EXCURSION_ATR=.75
ZONE_ATR=.25


def advance(observation,bars,now_ms):
    """Use the original range and zone when following an existing observation."""
    from TTW_BOT_V2 import validate_series
    result=deepcopy(observation)
    if result.get('resolution'):
        return result
    rows=[b for b in bars if b.open_time>=result['third_open_time'] and b.close_time<now_ms]
    if not rows:
        return result
    if rows[0].open_time!=result['third_open_time'] or validate_series(rows)!=rows:
        result['resolution_coverage']='UNKNOWN'
        return result
    result['resolution_coverage']='CONTIGUOUS_COMPLETED_CANDLES'
    for bar in rows:
        status=('UPPER_BOUNDARY_ACCEPTED' if bar.close>result['range_high']+result['zone'] else
                'LOWER_BOUNDARY_ACCEPTED' if bar.close<result['range_low']-result['zone'] else None)
        if status:
            result.update(phase='RESOLVED',known_at_ms=bar.close_time+1,
                          resolution=dict(status=status,known_at_ms=bar.close_time+1,price=bar.close))
            break
    return result


def scan(bars, tick, now_ms):
    from TTW_BOT_V2 import validate_series
    observed=[b for b in bars if b.open_time<=now_ms]
    if validate_series(observed)!=observed:
        return []
    closed=[b for b in bars if b.close_time < now_ms][-WINDOW:]
    if (len(closed)<11 or validate_series(closed)!=closed or not math.isfinite(tick) or tick<=0):
        return []
    pivots=[]
    # Each pivot is assessed only at the time its two right candles close.
    for i in range(2,len(closed)-RIGHT_BARS):
        b=closed[i]; local=closed[i-2:i+RIGHT_BARS+1]
        history=closed[max(0,i-14):i]
        ranges=[max(r.high-r.low,abs(r.high-a.close),abs(r.low-a.close))
                for a,r in zip(history,history[1:])]
        if not ranges:
            continue
        scale=sum(ranges)/len(ranges)
        for kind,price,is_extreme in (
                ('HIGH',b.high,b.high==max(x.high for x in local) and
                 b.high>max(x.high for x in local[:2]) and b.high>=max(x.high for x in local[3:])),
                ('LOW',b.low,b.low==min(x.low for x in local) and
                 b.low<min(x.low for x in local[:2]) and b.low<=min(x.low for x in local[3:]))):
            if is_extreme and scale>0:
                pivots.append(dict(kind=kind,price=price,index=i,open_time=b.open_time,
                                   known_at_ms=closed[i+RIGHT_BARS].close_time+1,atr=scale))
    pivots.sort(key=lambda p:(p['index'],p['kind']))
    # One bar that is both an outside high and low cannot certify an order.
    ambiguous={p['index'] for p in pivots if sum(q['index']==p['index'] for q in pivots)>1}
    pivots=[p for p in pivots if p['index'] not in ambiguous]
    alternating=[]
    for pivot in pivots:
        if alternating and pivot['kind']==alternating[-1]['kind']:
            old=alternating[-1]
            if (pivot['price']>old['price'] if pivot['kind']=='HIGH' else pivot['price']<old['price']):
                alternating[-1]=pivot
        else:
            alternating.append(pivot)
    results=[]
    for kind in ('HIGH','LOW'):
        # Need two same-side attempts separated by a confirmed opposite swing.
        candidates=[]
        for j in range(len(alternating)-2):
            a,other,b=alternating[j:j+3]
            if a['kind']!=kind or b['kind']!=kind:
                continue
            scale=min(a['atr'],b['atr'])
            zone=max(2*tick,ZONE_ATR*scale)
            if abs(a['price']-b['price'])>2*zone:
                continue
            excursion=min(abs(a['price']-other['price']),abs(b['price']-other['price']))
            if excursion<max(8*tick,MIN_EXCURSION_ATR*scale):
                continue
            candidates.append((j,a,other,b,scale,zone))
        if not candidates:
            continue
        for j,a,other,b,scale,zone in candidates[-3:]:
            # A second real excursion must occur after the second tested extreme.
            after=closed[b['index']+1:]
            if not after:
                continue
            retreated=next((r for r in after if
                           (a['price']-r.low if kind=='HIGH' else r.high-a['price']) >=
                           max(8*tick,MIN_EXCURSION_ATR*scale)),None)
            if retreated is None:
                continue
            low=min(a['price'],b['price'])-zone
            high=max(a['price'],b['price'])+zone
            live=[r for r in bars if r.open_time<=now_ms<=r.close_time]
            usable=after + live[-1:]
            third=next((r for r in usable if r.open_time>retreated.open_time and
                        (r.high>=low if kind=='HIGH' else r.low<=high)),None)
            if third is None:
                continue
            observed=max(b['known_at_ms'],retreated.close_time+1)
            # The third touch cannot predate when the two-anchor range is knowable.
            if third.open_time<observed:
                continue
            range_low=other['price'] if kind=='HIGH' else min(a['price'],b['price'])
            range_high=max(a['price'],b['price']) if kind=='HIGH' else other['price']
            history_to_third=[r for r in closed if r.open_time>=third.open_time]
            resolution=None
            for r in history_to_third:
                if r.close>range_high+zone:
                    resolution=dict(status='UPPER_BOUNDARY_ACCEPTED',known_at_ms=r.close_time+1,price=r.close)
                    break
                if r.close<range_low-zone:
                    resolution=dict(status='LOWER_BOUNDARY_ACCEPTED',known_at_ms=r.close_time+1,price=r.close)
                    break
            # Reject/accept is descriptive. Never turn the count into a trade order.
            identity=f"{kind}|{a['open_time']}|{b['open_time']}|{third.open_time}"
            results.append(dict(policy=POLICY,kind='THREE_TAP_STRUCTURE',side=kind,
                        identity=hashlib.sha256(identity.encode()).hexdigest()[:20],
                        phase='RESOLVED' if resolution else 'THIRD_VISIT',
                        known_at_ms=max(observed,third.close_time+1 if third.close_time<now_ms else now_ms,
                                        resolution['known_at_ms'] if resolution else 0),
                        range_known_at_ms=observed,observed_at_ms=now_ms,
                        range_low=range_low,range_high=range_high,
                        zone=zone,anchors=[dict(a),dict(other),dict(b)],
                        third_open_time=third.open_time,resolution=resolution,
                        scope='OBSERVE_ONLY',entry_eligible=False,
                        parameters=dict(right_bars=RIGHT_BARS,min_excursion_atr=MIN_EXCURSION_ATR,zone_atr=ZONE_ATR)))
    return results
