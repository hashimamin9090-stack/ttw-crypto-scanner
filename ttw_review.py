"""Opt-in, bounded private diagnostics. Never delivers alerts or changes trades.

TTW_REVIEW_REQUEST is an operator-supplied JSON request, not a code or URL input.
It exports only allowlisted signal evidence to existing application logs. It is
disabled by default, runs once per request/feed, and uses the normal paced API.
"""
import asyncio
from dataclasses import asdict, replace
import json
import logging
import os
import re
import time

import TTW_BOT_V2 as infra
import ttw_outcomes as outcomes

HOUR = 3600000
MAX_RECORDS = 128
SETUP_FIELDS = ('strategy_version', 'market', 'symbol', 'timeframe', 'direction',
                'entry', 'sl1', 'sl2', 'tp1', 'tp2', 'candle3_open_time',
                'candle3_close_time', 'tick_size', 'entry_turn')


def request_from_env(now):
    raw = os.getenv('TTW_REVIEW_REQUEST', '')
    if not raw:
        return None
    if len(raw) > 4096:
        raise ValueError('Review request too large')
    request = json.loads(raw)
    if (not isinstance(request, dict) or
            not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request.get('id', ''))):
        raise ValueError('Invalid review request identity')
    start, end = request.get('start_ms'), request.get('end_ms')
    if (type(start) is not int or type(end) is not int or
            not now-7*24*HOUR <= start < end <= now):
        raise ValueError('Invalid review window')
    cases = request.get('cases', [])
    if not isinstance(cases, list) or len(cases) > 4:
        raise ValueError('Too many replay cases')
    for case in cases:
        if (not isinstance(case, dict) or case.get('market') not in ('BINANCE_SPOT', 'BINANCE_FUTURES')
                or not re.fullmatch(r'[A-Z0-9]{2,20}USDT', case.get('symbol', ''))
                or case.get('timeframe') not in ('2H', '3H', '4H', '5H', '6H', '8H')
                or case.get('direction') not in ('BULLISH', 'BEARISH')
                or type(case.get('open_ms')) is not int):
            raise ValueError('Invalid replay case')
        duration = int(case['timeframe'][:-1])*HOUR
        if (case['open_ms'] % duration or case['open_ms'] < now-48*HOUR
                or case['open_ms']+duration > end):
            raise ValueError('Replay must use a completed recent candle')
    return request


def signal_rows(scanner, request):
    selected = []
    for key, record in scanner.store.data['alerts'].items():
        observed = record.get('observed_at_ms', 0)
        if (not scanner.owns_record(record) or record.get('status') != 'sent'
                or not request['start_ms'] <= observed <= request['end_ms']):
            continue
        setup = record['setup']
        selected.append(dict(key=key, observed_at_ms=observed,
            setup={k: setup[k] for k in SETUP_FIELDS if k in setup},
            paths=outcomes.summary(record) if record.get('trade_tracking') else None,
            tracking_last_ms=record.get('trade_tracking', {}).get('last_ms'),
            close_verdict=record.get('close_verdict'),
            post_alert_high=record.get('post_alert_high'), post_alert_low=record.get('post_alert_low')))
    selected.sort(key=lambda row: (row['observed_at_ms'], row['key']))
    return selected[:MAX_RECORDS], len(selected)


def emit(kind, request, scanner, data):
    logging.info('TTW_REVIEW_%s %s', kind, json.dumps(dict(
        review_id=request['id'], market=scanner.market_name, **data), allow_nan=False))


async def replay_case(scanner, request, case):
    from ttw_v3 import make_plan, geometry
    symbol, tf, direction = case['symbol'], case['timeframe'], case['direction']
    if symbol not in scanner.symbols:
        raise ValueError('Replay symbol is not in the active feed')
    factor = int(tf[:-1]); duration = factor*HOUR
    start = case['open_ms']; end = start+duration-1
    hours = await scanner.fetch_span(symbol, '1h', start-2*duration, end)
    trio = infra.aggregate_candles(hours, factor, 'hour')
    if len(trio) != 3 or trio[-1].open_time != start:
        raise ValueError('Replay aggregation mismatch')
    tick = scanner.tick_sizes[symbol]
    fraction, cap = scanner.geometry_limits(tf)
    opening = replace(trio[-1], high=trio[-1].open, low=trio[-1].open, close=trio[-1].open)
    plan = make_plan(trio[:2]+[opening], direction, tick, fraction, cap,
        scanner.cfg.impulse_range_fraction, scanner.cfg.rejection_wick_fraction,
        scanner.cfg.max_open_gap_range, c2_wick_contact=scanner.uses_wick_contact_policy(symbol))
    minutes = await scanner.fetch_span(symbol, '1m', start, end)
    event = None
    if plan:
        adapted = infra.TouchPlan(plan.direction, plan.contact, plan.upper-plan.lower,
                                  plan.impulse_distance, plan.candle3_open)
        event, _ = await scanner.walk_sequence(symbol, minutes, adapted, end, '1m')
    key = f'{symbol}|{tf}|{direction}|{start}'
    emit('CASE', request, scanner, dict(key=key, candles=[asdict(b) for b in trio],
         plan=asdict(plan) if plan else None, first_event=event,
         final_geometry=geometry(trio, direction, tick, fraction, cap,
             c2_wick_contact=scanner.uses_wick_contact_policy(symbol)),
         sequence_rejection=scanner.store.data['sequence_rejections'].get(key),
         historical_minute_known_time='CLOSE_PLUS_ONE_MODEL_NOT_ORIGINAL_RECEIPT'))
    for offset in range(0, len(minutes), 20):
        emit('MINUTES', request, scanner, dict(key=key, offset=offset,
             candles=[asdict(b) for b in minutes[offset:offset+20]]))


async def worker(scanner):
    """A failed diagnostic is isolated from streaming and signal workers."""
    try:
        request = request_from_env(int(time.time()*1000))
        if not request:
            await scanner.stop_event.wait()
            return
        done_key = request['id']+'|'+scanner.market_name
        completed = scanner.store.data.setdefault('private_reviews_completed', {})
        while not scanner.stop_event.is_set() and (not scanner.symbols or not scanner.stream_connected):
            await asyncio.sleep(1)
        if scanner.stop_event.is_set():
            return
        if done_key not in completed:
            rows, total = signal_rows(scanner, request)
            for row in rows:
                emit('SIGNAL', request, scanner, row)
            emit('COUNT', request, scanner, dict(selected=total, exported=len(rows),
                 truncated=total>MAX_RECORDS, start_ms=request['start_ms'],
                 end_ms=request['end_ms'], snapshot_ms=int(time.time()*1000)))
            for case in request.get('cases', []):
                if case['market'] == scanner.market_name:
                    await replay_case(scanner, request, case)
            completed[done_key] = int(time.time()*1000)
            for old in list(completed)[:-16]:
                completed.pop(old)
            scanner.store.save()
            emit('DONE', request, scanner, {})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Never log request bodies, arbitrary exception text or credentials.
        logging.warning('TTW_REVIEW_FAILED market=%s cause=%s', scanner.market_name, type(exc).__name__)
    await scanner.stop_event.wait()
