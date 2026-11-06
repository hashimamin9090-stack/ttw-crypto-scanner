import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import ttw_review as review
import ttw_v3 as bot
from futures_feed import trade_sequence
from test_ttw_v371 import sndk_watch


class ReviewBounds(unittest.TestCase):
    now = 1794000000000

    def request(self, **changes):
        data = dict(id='audit_20261106', start_ms=self.now-24*review.HOUR,
                    end_ms=self.now-1, cases=[])
        data.update(changes)
        with patch.dict(os.environ, TTW_REVIEW_REQUEST=json.dumps(data)):
            return review.request_from_env(self.now)

    def test_disabled_and_bounded_request(self):
        with patch.dict(os.environ, TTW_REVIEW_REQUEST=''):
            self.assertIsNone(review.request_from_env(self.now))
        self.assertEqual(self.request()['id'], 'audit_20261106')
        for changes in (dict(id='../../secret'), dict(end_ms=self.now+1),
                        dict(start_ms=self.now-8*24*review.HOUR), dict(cases=[{}]*5)):
            with self.assertRaises(ValueError):
                self.request(**changes)

    def test_replay_only_completed_recent_allowlisted_cases(self):
        case = dict(market='BINANCE_FUTURES', symbol='SNDKUSDT', timeframe='2H',
                    direction='BEARISH', open_ms=1793973600000)
        self.assertEqual(len(self.request(cases=[case])['cases']), 1)
        for changes in (dict(symbol='https://example.com'), dict(timeframe='1m'),
                        dict(open_ms=case['open_ms']+1), dict(market='PRIVATE'),
                        dict(open_ms=1794002400000)):
            with self.assertRaises(ValueError):
                self.request(cases=[dict(case, **changes)])

    def test_export_excludes_recipients_credentials_and_other_feeds(self):
        record = dict(status='sent', observed_at_ms=100, setup=dict(symbol='SNDKUSDT',
                      entry=900, token='secret'), deliveries={'12345': 'private'},
                      subscriber_id='67890', telegram_token='secret', market='FUTURES')
        other = dict(record, market='SPOT')
        scanner = SimpleNamespace(store=SimpleNamespace(data={'alerts': {'a':record, 'b':other}}),
                                  owns_record=lambda row:row['market']=='FUTURES')
        original = deepcopy(scanner.store.data)
        rows, total = review.signal_rows(scanner, dict(start_ms=1, end_ms=200))
        self.assertEqual(total, 1)
        output = json.dumps(rows)
        for sensitive in ('12345', '67890', 'secret', 'deliveries', 'telegram_token'):
            self.assertNotIn(sensitive, output)
        self.assertEqual(scanner.store.data, original)


class SequenceEvidence(unittest.TestCase):
    def test_live_pre_touch_failure_retains_actual_trigger(self):
        w = sndk_watch()
        w.plan = replace(w.plan, direction='BEARISH', candle3_open=100,
                         lower=105, upper=106, impulse_distance=3)
        w.extreme=100
        w.last_ms=0; w.touch_ms=0
        w.observe(97, 1000)
        self.assertEqual(w.failed, 'IMPULSE_BEFORE_TOUCH')
        self.assertEqual(w.failure_evidence['event_price'], 97)
        self.assertEqual(w.failure_evidence['running_extreme'], 100)
        self.assertEqual(w.failure_evidence['touch_ms'], 0)

    def test_futures_historical_failure_retains_same_event(self):
        p = bot.infra.TouchPlan('BEARISH', 105, 1, 3, 100)
        event, extreme = trade_sequence([dict(p=100,T=1),dict(p=97,T=2)], p, 100)
        self.assertEqual(event['reason'], 'IMPULSE_BEFORE_TOUCH')
        self.assertEqual(event['event_ms'], 2)
        self.assertEqual(event['event_price'], 97)
        self.assertEqual(extreme, 100)


class ReviewWorker(unittest.IsolatedAsyncioTestCase):
    async def test_case_aggregates_hours_and_emits_bounded_minute_chunks(self):
        h=review.HOUR; start=4*h
        hours=[bot.Candle(i*h,100,102,98,101,1,(i+1)*h-1) for i in range(6)]
        minutes=[bot.Candle(start+i*60000,100,102,98,101,1,start+(i+1)*60000-1) for i in range(120)]
        scanner=SimpleNamespace(market_name='BINANCE_FUTURES', symbols=['SNDKUSDT'],
            tick_sizes={'SNDKUSDT':.01}, geometry_limits=lambda tf:(.25,.15),
            uses_wick_contact_policy=lambda s:True,
            cfg=SimpleNamespace(impulse_range_fraction=.5,rejection_wick_fraction=.1,max_open_gap_range=1.5),
            store=SimpleNamespace(data={'sequence_rejections':{}}),
            fetch_span=AsyncMock(side_effect=[hours,minutes]))
        case=dict(symbol='SNDKUSDT',timeframe='2H',direction='BEARISH',open_ms=start)
        with patch.object(review,'emit') as emit, patch.object(bot,'make_plan',return_value=None):
            await review.replay_case(scanner,dict(id='test'),case)
        self.assertEqual([c.args[0] for c in emit.call_args_list].count('CASE'),1)
        self.assertEqual([c.args[0] for c in emit.call_args_list].count('MINUTES'),6)
        self.assertTrue(all(len(c.args[3]['candles'])<=20 for c in emit.call_args_list if c.args[0]=='MINUTES'))

    async def test_failure_never_stops_scanner_or_sends_messages(self):
        scanner = SimpleNamespace(stop_event=asyncio.Event(), market_name='BINANCE_SPOT')
        with patch.dict(os.environ, TTW_REVIEW_REQUEST='bad-json'):
            with self.assertLogs(level='WARNING') as logs:
                task = asyncio.create_task(review.worker(scanner))
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                scanner.stop_event.set()
                await task
        self.assertIn('JSONDecodeError', logs.output[0])
        self.assertNotIn('bad-json', logs.output[0])

    async def test_request_runs_once_per_feed(self):
        stop = asyncio.Event()
        store = SimpleNamespace(data={'alerts':{},'private_reviews_completed':{}}, save=lambda:None)
        scanner = SimpleNamespace(stop_event=stop, market_name='BINANCE_SPOT',
                                  store=store, symbols=['BTCUSDT'], stream_connected=True)
        request = dict(id='review1', start_ms=1, end_ms=2, cases=[])
        with patch.object(review, 'request_from_env', return_value=request), patch.object(review,'emit') as emit:
            for _ in range(2):
                stop.clear()
                task=asyncio.create_task(review.worker(scanner))
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                stop.set()
                await task
            self.assertEqual([c.args[0] for c in emit.call_args_list], ['COUNT','DONE'])


if __name__ == '__main__':
    unittest.main()
