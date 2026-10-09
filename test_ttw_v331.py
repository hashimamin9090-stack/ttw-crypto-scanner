import asyncio
from dataclasses import replace
import os
import tempfile
from unittest.mock import patch
import unittest

import ttw_context as context
import ttw_v3 as bot
from test_ttw_v3 import candle, fixture, reciprocal, live_watch
from test_ttw_v33 import history, structural_bars, setup
import test_ttw_v33 as legacy


class PrecisionRules(unittest.TestCase):
    def config(self, **values):
        with patch.dict(os.environ, dict(TELEGRAM_CHAT_ID='test',
                                       TELEGRAM_BOT_TOKEN='test', **values), clear=True):
            return bot.Config()

    def bars_setup(self, bear=False):
        bars, s = structural_bars(), setup()
        bars[:-3] = [replace(b, open=104.5, close=104.5, high=110, low=99)
                     for b in bars[:-3]]
        if bear:
            bars = [replace(b, open=200-b.open, high=200-b.low,
                            low=200-b.high, close=200-b.close) for b in bars]
            s.update(direction='BEARISH')
            for name in ('entry', 'sl1', 'sl2', 'tp1', 'tp2', 'wick3'):
                s[name] = 200-s[name]
        return bars, s

    def quality(self, bars, s):
        return context.entry_quality(bars, [], s,
            {'ok': True, 'regime': 'ORDERLY_OR_RANGE'}, precision=True)

    def test_default_and_old_env_override_both_enforce_point_one_cap(self):
        for value in ('.1', '1.0'):
            cfg = self.config(V3_GEOMETRY_PRICE_CAP_PCT=value)
            self.assertEqual(cfg.geometry_price_cap_pct, .1)
            self.assertTrue(cfg.precision_enabled)

    def test_large_miss_rejected_in_both_directions_even_with_long_wicks(self):
        bars = [candle(0,105,107,100,104), candle(3600000,104,107,101,105),
                candle(7200000,105,107,101,106)]
        for bear in (False, True):
            rows = list(map(reciprocal, bars)) if bear else bars
            side = 'BEARISH' if bear else 'BULLISH'
            self.assertIsNotNone(bot.geometry(rows, side, .01))
            self.assertIsNone(bot.geometry(rows, side, .01, price_cap_pct=.1))

    def test_small_symmetric_misses_and_body_clearance_still_apply(self):
        for delta in (-.04, .04):
            bars = fixture(); bars[1] = replace(bars[1], low=99+delta)
            for bear in (False, True):
                rows = list(map(reciprocal, bars)) if bear else bars
                self.assertIsNotNone(bot.geometry(rows, 'BEARISH' if bear else 'BULLISH',
                                                  .001, price_cap_pct=.1))
        bars = fixture(); bars[1] = replace(bars[1], open=99, close=99)
        self.assertIsNone(bot.geometry(bars, 'BULLISH', .001, price_cap_pct=.1))

    def test_incidental_range_edge_without_departure_does_not_qualify(self):
        rows = [replace(b, open=99.2, close=99.2) for b in history(24)]
        self.assertTrue(context.established_levels(rows, 0)[0])
        self.assertEqual(context.established_levels(rows, 0, strong=True, tick=.01)[0], [])

    def level_rows(self):
        rows = [replace(b, open=105, close=105, high=110, low=100)
                for b in history(24)]
        rows[3] = replace(rows[3], low=99)
        return rows

    def test_body_acceptance_retires_level_and_completed_reclaim_restores_it(self):
        rows = self.level_rows()
        for i in range(10,24):
            rows[i] = replace(rows[i], open=97, close=97, low=96, high=98)
        supports = context.established_levels(rows, 0, strong=True, tick=.01)[0]
        self.assertFalse(any(level == 99 for level, *_ in supports))
        for i in range(12,24):
            rows[i] = replace(rows[i], open=105, close=105, low=100, high=110)
        supports = context.established_levels(rows, 0, strong=True, tick=.01)[0]
        reclaimed = [v for v in supports if v[0] == 99]
        self.assertTrue(reclaimed)
        self.assertTrue(reclaimed[0][1].endswith('_RECLAIMED'))
        self.assertGreaterEqual(reclaimed[0][2], rows[13].close_time)

    def test_wick_sweep_does_not_retire_level(self):
        rows = self.level_rows()
        rows[10] = replace(rows[10], low=97)
        supports = context.established_levels(rows, 0, strong=True, tick=.01)[0]
        self.assertTrue(any(v[0] == 99 and not v[1].endswith('_RECLAIMED') for v in supports))

    def test_future_departure_cannot_create_a_level_at_cutoff(self):
        rows = [replace(b, open=99.2, close=99.2) for b in history(24)]
        future = candle(0,100,105,99,104,900000)
        self.assertEqual(context.established_levels(rows+[future],0,strong=True,tick=.01)[0], [])

    def test_both_sides_can_pass_with_clear_path_and_protective_stops(self):
        for bear in (False, True):
            bars, s = self.bars_setup(bear)
            r = self.quality(bars, s)
            self.assertTrue(r['ok'], r)
            self.assertGreaterEqual(r['risk_checks']['sl2']['target_r'], 1.5)

    def test_wide_c2_stop_rejects_even_when_c3_stop_is_usable(self):
        for bear in (False, True):
            bars, s = self.bars_setup(bear)
            s['sl2'] = 105 if bear else 95
            self.assertEqual(self.quality(bars,s)['reason'], 'C2_TARGET_TOO_SMALL_FOR_RISK')

    def test_tp1_beyond_major_opposition_rejects_both_directions(self):
        for bear in (False, True):
            bars, s = self.bars_setup(bear)
            s['tp1'] = 89 if bear else 111
            self.assertEqual(self.quality(bars,s)['reason'], 'TARGET_BEYOND_OPPOSING_LEVEL')

    def test_gold_tokens_excluded_and_tao_retained(self):
        self.assertTrue({'XAUT','PAXG'} <= bot.infra.EXCLUDED_BASES)
        self.assertNotIn('TAO', bot.infra.EXCLUDED_BASES)

    def test_timeframes_parents_targets_and_chart_intervals(self):
        self.assertEqual(bot.TIMEFRAMES['8H'], ('8h',1,'native'))
        self.assertEqual(bot.TIMEFRAMES['16H'], ('1h',16,'hour'))
        for tf, interval in (('8H','480'),('16H','960')):
            self.assertEqual(context.PARENTS[tf], '1D')
            self.assertEqual(bot.infra.TV_INTERVAL[tf], interval)
            self.assertEqual(bot.targets(100,'BULLISH',tf), (105,110.00000000000001,5,10))

    def test_sixteen_hour_buckets_keep_complete_history_and_live_tail(self):
        rows = [candle(i*3600000,100,102,99,101) for i in range(16*8+5)]
        bars = bot.infra.aggregate_candles(rows,16,'hour')
        self.assertEqual(len(bars),9)
        self.assertEqual(bars[0].close_time-bars[0].open_time+1,16*3600000)
        self.assertEqual(bars[-1].open_time,16*8*3600000)
        self.assertEqual(bars[-1].volume,5)
        self.assertEqual(bars[-1].close_time,16*9*3600000-1)


class PrecisionDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_real_precision_rules_deliver_both_sides_on_added_timeframes(self):
        helper = legacy.QualityIntegration()
        for tf in ('8H','16H'):
            duration = int(tf[:-1])*3600000
            for bear in (False,True):
                with tempfile.TemporaryDirectory() as folder:
                    scanner = helper.scanner(folder)
                    scanner.cfg.precision_enabled = True
                    scanner.stream_connected = True
                    w = helper.qualifying_watch(bear)
                    w.timeframe = tf
                    # Re-anchor all candles so causal histories and the live
                    # trigger use the actual new timeframe duration.
                    w.bars = [replace(b, open_time=b.open_time//3600000*duration,
                                      close_time=(b.open_time//3600000+1)*duration-1)
                              for b in w.bars]
                    now = 2*duration+4000
                    w.key = 'TESTUSDT|'+tf+'|'+w.plan.direction+'|'+str(2*duration)
                    w.verified_ms = 2*duration
                    w.touch_ms = 2*duration+1000
                    w.last_ms = now
                    w.plan = replace(w.plan,price_cap_pct=.1)
                    w.parent_bars = []
                    scanner.btc_bars = {'15m':history(24,end=now//900000*900000)+[
                        candle(now//900000*900000,100,100.2,99.8,100,900000)],
                        '1h':history(24,3600000,end=now//3600000*3600000)}
                    scanner.last_quotes['BTCUSDT'] = (now,100)
                    scanner.symbol_last_id[w.symbol] = 10
                    calls = []
                    async def telegram(method,payload):
                        calls.append((method,payload))
                        return {'ok':True,'result':{'message_id':123}}
                    scanner.telegram_call = telegram
                    self.assertTrue(w.ready(now))
                    with patch.object(bot.time,'time',return_value=now/1000):
                        await scanner.send_watch(w)
                    self.assertTrue(w.alerted,dict(scanner.stats))
                    self.assertEqual(len(calls),1)
                    self.assertTrue(scanner.store.data['alerts'][w.key]['entry_quality']['ok'])
                    self.assertIn('risk_checks', scanner.store.data['alerts'][w.key]['entry_quality'])

    async def test_explicit_universe_omits_gold_preserves_private_recipient(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = legacy.QualityIntegration().scanner(folder)
            scanner.cfg.symbol_override = ['XAUT','PAXG','BTC','ETH']
            scanner.cfg.top_n = 25
            async def get(*args,**kwargs):
                return {'symbols':[]}
            scanner._get_json = get
            await scanner.refresh_universe()
            self.assertEqual(scanner.symbols,['TAOUSDT','BTCUSDT','ETHUSDT'])
            self.assertEqual(scanner.recipient_ids(),['private-test'])
