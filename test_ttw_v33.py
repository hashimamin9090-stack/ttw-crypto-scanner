import asyncio
from dataclasses import replace
import math
import os
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ttw_v3 as bot
import ttw_context as context
import ttw_outcomes as outcomes
from test_ttw_v3 import live_watch, candle, reciprocal


def history(n=24, duration=900000, end=0):
    return [candle(end+(i-n)*duration, 100, 101, 99, 100, duration) for i in range(n)]


def btc_rows():
    short = history(24)+[candle(0, 100, 100.2, 99.8, 100, 900000)]
    hours = history(24, 3600000)
    return short, hours


def structural_bars():
    prior = [replace(b, high=102) for b in history(24, 3600000)]
    # Support at 99 was known before C1; C3 is near it and reclaims its open.
    anchors = [candle(0, 100.8, 101, 99, 100.4),
               candle(3600000, 100.4, 100.5, 99, 99.7),
               candle(7200000, 99.7, 100, 99, 99.9)]
    return prior+anchors


def setup():
    return dict(symbol='TESTUSDT', timeframe='4H', direction='BULLISH',
                entry=99.9, sl1=98.9, sl2=98.8, tp1=102.9, tp2=104.9,
                wick3=99, tick_size=.01, candle3_open_time=7200000,
                candle3_close_time=10799999)


class BTCContext(unittest.TestCase):
    def test_live_dump_blocks_longs_and_live_rally_blocks_shorts(self):
        short, hours = btc_rows()
        for price, side, regime in [(97.5, 'BULLISH', 'DOWN_EXPANSION'),
                                    (102.5, 'BEARISH', 'UP_EXPANSION')]:
            c = context.btc_context(short, hours, price, 1000, 1000)
            self.assertEqual(c['regime'], regime)
            self.assertFalse(context.btc_allows(c, side))
            self.assertTrue(context.btc_allows(c, 'BEARISH' if side == 'BULLISH' else 'BULLISH'))

    def test_old_bearish_trend_does_not_block_stabilised_bullish_reversal(self):
        short, hours = btc_rows()
        hours[-1] = replace(hours[-1], low=95, close=96)
        c = context.btc_context(short, hours, 100.1, 1000, 1000)
        self.assertEqual(c['trend'], 'DOWN')
        self.assertTrue(context.btc_allows(c, 'BULLISH'))

    def test_closed_breakout_continuation_is_blocked_even_after_new_bar_open(self):
        short, hours = btc_rows()
        short[-3] = replace(short[-3], high=101, low=97, close=98)
        short[-2] = replace(short[-2], open=98, high=98.5, low=96, close=96.5)
        short[-1] = replace(short[-1], open=96.5, high=96.7, low=96, close=96.1)
        c = context.btc_context(short, hours, 96.1, 1000, 1000)
        self.assertEqual(c['regime'], 'DOWN_EXPANSION')

    def test_stale_missing_gapped_and_future_quotes_are_unknown(self):
        short, hours = btc_rows()
        tests = [(short, 0, 10000), (short[:3], 1000, 1000),
                 ([replace(short[0], close_time=-2)]+short[1:], 1000, 1000),
                 (short, 10000, 1000)]
        for bars, quote, now in tests:
            c = context.btc_context(bars, hours, 100, quote, now)
            self.assertFalse(c['ok'])
            self.assertFalse(context.btc_allows(c, 'BULLISH'))

    def test_btc_data_is_checked_at_entry_not_just_when_watch_was_created(self):
        short, hours = btc_rows()
        safe = context.btc_context(short, hours, 100.1, 1000, 1000)
        danger = context.btc_context(short, hours, 97.5, 2000, 2000)
        self.assertTrue(context.btc_allows(safe, 'BULLISH'))
        self.assertFalse(context.btc_allows(danger, 'BULLISH'))


class LocationQuality(unittest.TestCase):
    def test_tick_floor_fits_default_early_entry_window(self):
        bars = structural_bars()
        bars[-3:-1] = [replace(b, open=99.02, high=99.03, low=99,
                               close=99.02) for b in bars[-3:-1]]
        self.assertEqual(context.reclaim_distance(bars, 'BULLISH', .01), .02)
    def test_established_support_and_symmetric_resistance(self):
        bars = structural_bars()
        c = context.location_context(bars, [], 'BULLISH', 99.9, 99, .01)
        self.assertTrue(c['ok'])
        self.assertLess(c['level']['known_at_ms'], bars[-3].open_time)
        reflected = [replace(b, open=200-b.open, high=200-b.low,
                             low=200-b.high, close=200-b.close) for b in bars]
        self.assertTrue(context.location_context(reflected, [], 'BEARISH', 100.1, 101, .01)['ok'])

    def test_c1_c2_cannot_create_their_own_old_level(self):
        bars = structural_bars()
        bars[-3:] = [replace(b, low=95) for b in bars[-3:]]
        c = context.location_context(bars, [], 'BULLISH', 99.9, 95, .01)
        self.assertFalse(c['ok'])

    def test_incomplete_parent_or_future_parent_candles_do_not_create_levels(self):
        bars = structural_bars()
        parent = [replace(b, low=95) for b in history(12, end=bars[-1].open_time+12*3600000)]
        c = context.location_context(bars, parent, 'BULLISH', 99.9, 95, .01)
        self.assertFalse(c['ok'])

    def test_parent_support_can_qualify_setup(self):
        bars = structural_bars()
        bars[:-3] = [replace(b, low=98) for b in bars[:-3]]
        parent = history(24)
        c = context.location_context(bars, parent, 'BULLISH', 99.9, 99, .01)
        self.assertTrue(c['ok'])
        self.assertEqual(c['level']['source'], 'PARENT')

    def test_one_tick_reclaim_holds_and_meaningful_reclaim_passes(self):
        bars, s = structural_bars(), setup()
        safe = {'ok': True, 'regime': 'ORDERLY_OR_RANGE'}
        s['entry'] = bars[-1].open+.01
        self.assertEqual(context.entry_quality(bars, [], s, safe)['reason'], 'RECLAIM_TOO_SMALL')
        s['entry'] = 99.9
        self.assertTrue(context.entry_quality(bars, [], s, safe)['ok'])

    def test_target_below_risk_and_nearby_obstacle_are_rejected(self):
        bars, s = structural_bars(), setup()
        safe = {'ok': True, 'regime': 'ORDERLY_OR_RANGE'}
        s['tp1'] = 100.5
        self.assertEqual(context.entry_quality(bars, [], s, safe)['reason'], 'TARGET_TOO_SMALL_FOR_RISK')
        s['tp1'] = 103
        bars[:-3] = [replace(b, high=101) for b in bars[:-3]]
        # Nearest known resistance is 101, only 1.1R from this entry.
        self.assertEqual(context.entry_quality(bars, [], s, safe)['reason'], 'OPPOSING_LEVEL_TOO_CLOSE')

    def test_held_watch_can_qualify_later_without_close_or_hold_timer(self):
        bars, s = structural_bars(), setup()
        unsafe = {'ok': True, 'regime': 'DOWN_EXPANSION'}
        safe = {'ok': True, 'regime': 'ORDERLY_OR_RANGE'}
        before = context.entry_quality(bars, [], s, unsafe, min_room_r=1)
        after = context.entry_quality(bars, [], s, safe, min_room_r=1)
        self.assertEqual(before['reason'], 'BTC_OPPOSING_EXPANSION')
        self.assertTrue(after['ok'])

    def test_reclaim_scaling_and_gapped_history(self):
        bars = structural_bars()
        scaled = [replace(b, open=b.open*10, high=b.high*10, low=b.low*10, close=b.close*10) for b in bars]
        self.assertAlmostEqual(context.reclaim_distance(scaled, 'BULLISH', .1),
                               10*context.reclaim_distance(bars, 'BULLISH', .01))
        bars[3] = replace(bars[3], close_time=bars[3].close_time-1)
        self.assertEqual(context.location_context(bars, [], 'BULLISH', 99.9, 99, .01)['reason'],
                         'LOCATION_HISTORY_MISSING')


class OutcomeAccounting(unittest.TestCase):
    def record(self, bear=False):
        s = setup()
        if bear:
            s.update(direction='BEARISH', entry=100.1, sl1=101.1, sl2=101.2,
                     tp1=97.1, tp2=95.1)
        return dict(setup=s, trade_tracking=outcomes.start_tracking(s, 8000000, 10))

    def test_target_then_stop_does_not_rewrite_earlier_target(self):
        for bear in (False, True):
            r = self.record(bear)
            self.assertTrue(outcomes.observe(r, 97 if bear else 103, 11000000, 11))
            self.assertTrue(outcomes.observe(r, 102 if bear else 98, 12000000, 12))
            for path in r['trade_tracking']['paths'].values():
                self.assertEqual(path, {'tp1': 'TP', 'tp2': 'STOP'})

    def test_stop_then_rally_stays_stopped(self):
        r = self.record()
        outcomes.observe(r, 98, 11000000, 11)
        outcomes.observe(r, 105, 12000000, 12)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'], 'STOP')

    def test_two_stops_have_independent_results(self):
        r = self.record()
        outcomes.observe(r, 98.85, 11000000, 11)
        outcomes.observe(r, 105, 12000000, 12)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'], 'STOP')
        self.assertEqual(r['trade_tracking']['paths']['sl2']['tp2'], 'TP')

    def test_observation_continues_after_c3_close(self):
        r = self.record()
        self.assertGreater(11000000, r['setup']['candle3_close_time'])
        outcomes.observe(r, 105, 11000000, 11)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp2'], 'TP')

    def test_sequence_gap_is_unknown_not_a_win_or_loss(self):
        r = self.record()
        self.assertTrue(outcomes.observe(r, 105, 11000000, 12))
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'], 'UNKNOWN')
        self.assertTrue(r['trade_tracking']['gap'])

    def test_gap_preserves_targets_already_observed(self):
        r = self.record()
        outcomes.observe(r, 103, 11000000, 11)
        outcomes.mark_gap(r, 11500000)
        self.assertEqual(r['trade_tracking']['paths']['sl1'], {'tp1': 'TP', 'tp2': 'UNKNOWN'})

    def test_duplicate_or_out_of_order_events_do_not_change_results(self):
        r = self.record()
        self.assertFalse(outcomes.observe(r, 105, 7000000, 11))
        self.assertFalse(outcomes.observe(r, 105, 11000000, 10))
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'], 'OPEN')

    def test_expiry_does_not_turn_a_late_target_into_a_win(self):
        r = self.record(); deadline = r['trade_tracking']['deadline_ms']
        self.assertTrue(outcomes.observe(r, 105, deadline+1, 11))
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'], 'EXPIRED')

    def test_pattern_label_is_separate_from_target_label(self):
        r = self.record(); r['close_verdict'] = 'CONFIRMED'
        self.assertEqual(outcomes.status_text(r), 'PATTERN CONFIRMED')
        outcomes.observe(r, 103, 11000000, 11)
        self.assertIn('SL1 TP1', outcomes.status_text(r))
        self.assertAlmostEqual(outcomes.summary(r)['paths']['sl1']['target1_r'], 3)


class MigrationState(unittest.TestCase):
    def test_private_seed_preserves_alerts_and_subscriber_but_local_state_wins(self):
        with tempfile.TemporaryDirectory() as d:
            seed = Path(d)/'seed.json'
            seed.write_text(json.dumps(dict(schema_version=2, alerts={'legacy':{'status':'sent'}},
                telegram_subscribers={'private-test':{'joined_at':1}}, telegram_offset=7)))
            target = Path(d)/'state'
            with patch.dict(os.environ,{'STATE_BOOTSTRAP_FILE':str(seed)}):
                store = bot.infra.StateStore(target)
                self.assertIn('legacy',store.data['alerts'])
                self.assertIn('private-test',store.data['telegram_subscribers'])
                self.assertEqual(store.data['telegram_offset'],7)
                store.data['telegram_subscribers'] = {}; store.save()
                again = bot.infra.StateStore(target)
                self.assertEqual(again.data['telegram_subscribers'],{})

    def test_missing_or_invalid_seed_fails_instead_of_silently_resending_old_alerts(self):
        with tempfile.TemporaryDirectory() as d:
            seed = Path(d)/'seed.json'
            with patch.dict(os.environ,{'STATE_BOOTSTRAP_FILE':str(seed)}):
                with self.assertRaises(FileNotFoundError):bot.infra.StateStore(Path(d)/'missing')
                seed.write_text('{"schema_version":99}')
                with self.assertRaises(ValueError):bot.infra.StateStore(Path(d)/'invalid')


class QualityIntegration(unittest.IsolatedAsyncioTestCase):
    def scanner(self, folder):
        return bot.Scanner(SimpleNamespace(telegram_chat_id='private-test', max_concurrency=2,
            state_dir=folder, stop_buffer_pct=.1, send_charts=False,
            max_entry_move_fraction=.25, quality_enabled=True, context_history=40,
            reclaim_fraction=.08, level_proximity=.35, min_target_r=1.5, min_room_r=1.25))

    async def test_quality_veto_does_not_kill_or_reserve_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); w = live_watch()
            scanner.stream_connected = True; scanner.watches[w.key] = w
            with patch.object(bot.time, 'time', return_value=7204):
                await scanner.send_watch(w)
            self.assertFalse(w.alerted)
            self.assertIn(w.key, scanner.watches)
            self.assertEqual(scanner.store.data['alerts'], {})

    async def test_c2_reference_matches_actual_c2_wick_even_if_not_outermost(self):
        w = live_watch()
        w.last_price = 99.2
        s = bot.make_setup(w, .1, c2_reference=True)
        self.assertAlmostEqual(s['sl2'], w.bars[-2].low*.999)
        self.assertEqual(s['stop2_basis'], 'C2_WICK')

    async def test_c2_on_wrong_side_uses_protective_outer_wick_both_directions(self):
        for bear in (False, True):
            w = live_watch(bear)
            s = bot.make_setup(w, .1, c2_reference=True)
            self.assertEqual(s['stop2_basis'], 'OUTER_WICK')
            self.assertTrue(s['sl2'] > s['entry'] if bear else s['sl2'] < s['entry'])
            self.assertIn('SL2 (outer)', bot.alert_payload(s, w.key)[0])

    def qualifying_watch(self, bear=False):
        w = live_watch(bear)
        prior = [replace(b, high=108, low=98.01, open=105, close=106)
                 for b in history(24, 3600000, end=-10800000)]
        if bear:
            prior = list(map(reciprocal, prior))
        w.bars = prior+w.bars
        w.observe(10000/98.4 if bear else 98.4, 7204000)
        return w

    def seed_btc(self, scanner):
        short = history(24, end=7200000)+[candle(7200000,100,100.2,99.8,100,900000)]
        scanner.btc_bars = {'15m':short, '1h':history(24,3600000,end=7200000)}
        scanner.last_quotes['BTCUSDT'] = (7204000,100)

    async def test_full_enabled_quality_delivers_live_setup_both_directions_once(self):
        for bear in (False, True):
            with tempfile.TemporaryDirectory() as d:
                scanner = self.scanner(d); w = self.qualifying_watch(bear)
                scanner.stream_connected = True
                self.seed_btc(scanner)
                scanner.symbol_last_id[w.symbol] = 10
                calls = []
                async def telegram(method, payload):
                    calls.append((method,payload)); return {'ok':True,'result':{'message_id':123}}
                scanner.telegram_call = telegram
                self.assertTrue(w.ready(7204000))
                with patch.object(bot.time,'time',return_value=7204):
                    await scanner.send_watch(w)
                self.assertTrue(w.alerted)
                self.assertFalse(w.ready(7204000))
                self.assertEqual(len(calls),1)
                record = scanner.store.data['alerts'][w.key]
                self.assertTrue(record['entry_quality']['ok'])
                self.assertEqual(record['trade_tracking']['last_trade_id'],10)
                scanner.symbols = [w.symbol]
                scanner.accept_trade(w.symbol,11,7204100,record['setup']['tp1'])
                self.assertEqual(record['trade_tracking']['paths']['sl1']['tp1'],'TP')

    async def test_trades_during_render_start_tracking_at_latest_contiguous_id(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.cfg.send_charts = True
            scanner.stream_connected = True; w = self.qualifying_watch()
            self.seed_btc(scanner); scanner.symbol_last_id[w.symbol] = 10
            async def render_thread(function, *args):
                scanner.symbol_last_id[w.symbol] = 12
                w.last_ms = 7204001
                return b'png'
            async def photo(payload, png):
                return {'ok':True,'result':{'message_id':123}}
            scanner.telegram_photo = photo
            with patch.object(bot.asyncio,'to_thread',side_effect=render_thread), patch.object(bot.time,'time',return_value=7204.001):
                await scanner.send_watch(w)
            record = scanner.store.data['alerts'][w.key]
            self.assertEqual(record['trade_tracking']['last_trade_id'],12)
            outcomes.observe(record,record['setup']['tp1'],7204100,13)
            self.assertEqual(record['trade_tracking']['paths']['sl1']['tp1'],'TP')

    async def test_stop_breach_and_return_during_render_cancels_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.cfg.send_charts = True
            scanner.stream_connected = True; w = self.qualifying_watch()
            self.seed_btc(scanner)
            async def render_thread(function,*args):
                w.bars[-1] = replace(w.bars[-1],low=97.91)
                return b'png'
            with patch.object(bot.asyncio,'to_thread',side_effect=render_thread), patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            self.assertFalse(w.alerted)
            self.assertEqual(scanner.store.data['alerts'],{})

    async def test_outcomes_remain_subscribed_when_pair_leaves_top25(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.symbols = ['OTHERUSDT']
            scanner.outcome_keys = {'TESTUSDT':{'active'}}
            self.assertIn('TESTUSDT',scanner.market_symbols())
            self.assertIn('BTCUSDT',scanner.market_symbols())

    async def test_vetoed_baseline_is_observed_once_without_a_telegram_alert(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.stream_connected = True
            scanner.symbols = ['TESTUSDT']; scanner.symbol_last_id['TESTUSDT'] = 10
            w = self.qualifying_watch()
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
                await scanner.send_watch(w)
            self.assertFalse(w.alerted)
            self.assertEqual(scanner.store.data['alerts'],{})
            rows = scanner.store.data['shadow_entries']
            self.assertEqual(len(rows),1)
            r = rows[w.key]
            self.assertTrue(r['hypothetical'])
            self.assertEqual(r['first_quality']['reason'],'BTC_DATA_UNAVAILABLE')
            scanner.accept_trade('TESTUSDT',11,7204100,r['setup']['tp1'])
            scanner.flush_shadows(7204100)
            self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'TP')
            self.assertFalse(scanner.dirty_shadows)

    async def test_vetoed_baseline_can_confirm_while_new_alert_remains_unpublished(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.stream_connected = True
            w = self.qualifying_watch()
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            await scanner.close_updates(w.symbol,w.timeframe,w.bars,10800000)
            self.assertEqual(scanner.store.data['shadow_entries'][w.key]['close_verdict'],'CONFIRMED')
            self.assertEqual(scanner.store.data['alerts'],{})

    async def test_delayed_quality_entry_keeps_first_baseline_and_both_frozen_stops(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.stream_connected = True
            scanner.symbols = ['TESTUSDT']; scanner.symbol_last_id['TESTUSDT'] = 10
            w = self.qualifying_watch()
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            baseline = scanner.store.data['shadow_entries'][w.key]
            first_entry, first_stop = baseline['setup']['entry'],baseline['setup']['sl1']
            self.seed_btc(scanner)
            w.observe(98.41,7204100)
            async def telegram(method,payload):
                return {'ok':True,'result':{'message_id':123}}
            scanner.telegram_call = telegram
            with patch.object(bot.time,'time',return_value=7204.1):
                await scanner.send_watch(w)
            self.assertTrue(w.alerted)
            self.assertEqual(baseline['setup']['entry'],first_entry)
            self.assertEqual(baseline['setup']['sl1'],first_stop)
            self.assertNotEqual(scanner.store.data['alerts'][w.key]['setup']['entry'],first_entry)

    async def test_shadow_stream_gap_is_unknown_and_does_not_block_real_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.stream_connected = True
            scanner.symbols = ['TESTUSDT']; scanner.symbol_last_id['TESTUSDT'] = 10
            w = self.qualifying_watch()
            with patch.object(bot.time,'time',return_value=7204):
                await scanner.send_watch(w)
            scanner.accept_trade('TESTUSDT',12,7204100,105)
            baseline = scanner.store.data['shadow_entries'][w.key]
            self.assertEqual(baseline['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')
            self.assertNotIn(w.key,scanner.store.data['alerts'])

    async def test_active_outcome_receives_trades_after_watch_expires(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.symbols = ['TESTUSDT']
            s = setup(); key = 'TESTUSDT|4H|BULLISH|7200000'
            scanner.store.reserve(key, dict(setup=s, trade_tracking=outcomes.start_tracking(s,8000000,10)))
            scanner.outcome_keys = {'TESTUSDT': {key}}
            scanner.symbol_last_id['TESTUSDT'] = 10
            scanner.accept_trade('TESTUSDT', 11, 11000000, 105)
            self.assertIn(key, scanner.dirty_outcomes)
            self.assertEqual(scanner.store.data['alerts'][key]['trade_tracking']['paths']['sl1']['tp2'], 'TP')

    async def test_reload_preserves_dedup_and_marks_unobserved_outcomes_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); s = setup(); key = 'test'
            scanner.store.reserve(key, dict(setup=s, trade_tracking=outcomes.start_tracking(s,8000000,10)))
            scanner.store.sent(key, 123)
            again = self.scanner(d)
            self.assertIn(key, again.store.data['alerts'])
            self.assertEqual(again.store.data['alerts'][key]['trade_tracking']['paths']['sl1']['tp1'], 'UNKNOWN')

    async def test_fresh_quality_check_is_repeated_after_chart_rendering(self):
        with tempfile.TemporaryDirectory() as d:
            scanner = self.scanner(d); scanner.cfg.send_charts = True
            scanner.stream_connected = True; w = live_watch()
            calls = []
            def quality(watch, s, now):
                calls.append(now)
                return {'ok': len(calls) == 1, 'reason': 'QUALITY_READY' if len(calls) == 1 else 'BTC_OPPOSING_EXPANSION'}
            scanner.quality_check = quality
            with patch.object(bot, 'render_chart', return_value=b'png'), patch.object(bot.time, 'time', return_value=7204):
                await scanner.send_watch(w)
            self.assertEqual(len(calls), 2)
            self.assertFalse(w.alerted)
            self.assertEqual(scanner.store.data['alerts'], {})


if __name__ == '__main__':
    unittest.main()
