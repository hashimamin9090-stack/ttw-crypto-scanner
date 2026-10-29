"""Causal intraday selection, horizon separation and conservative gap recovery."""
from copy import deepcopy
from dataclasses import asdict, replace
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import ttw_context as context
import ttw_outcomes as outcomes
import ttw_v3 as bot
from futures_feed import FuturesScanner
import test_ttw_v33 as legacy
from test_ttw_v354 import recorded_watch, seed
from test_ttw_v352 import scanner as btc_scanner


def bar(start, high=101, low=99, close=100, duration=1000):
    return bot.Candle(start, 100, high, low, close, 1, start+duration-1)


def record(base=0, duration=1000, bear=False):
    s = dict(symbol='TESTUSDT', timeframe='2H', direction='BULLISH', entry=100,
             sl1=98, sl2=97, tp1=102, tp2=104, candle3_open_time=base,
             candle3_close_time=base+duration-1, strategy_version=bot.VERSION)
    prefix = asdict(bar(base,100.5,99.5,duration=duration))
    if bear:
        s.update(direction='BEARISH',sl1=102,sl2=103,tp1=98,tp2=96)
    r = dict(status='sent', setup=s, market='BINANCE_SPOT', observed_at_ms=base+100,
             candles=[prefix], trade_tracking=outcomes.start_tracking(s,base+100,10))
    outcomes.mark_gap(r,base+300,'STREAM_RECONNECT')
    return r


class IntradayReclaim(unittest.TestCase):
    def test_broad_intraday_requires_price_recovery_both_directions(self):
        rows=[bar(0),bot.Candle(1000,102,103,98,98,1,1999),bar(2000)]
        approach={'approach_mode':'BROADER_REVERSAL'}
        for bear in (False,True):
            with self.subTest(bear=bear):
                bars=rows if not bear else [replace(b,open=200-b.open,high=200-b.low,
                                                  low=200-b.high,close=200-b.close) for b in rows]
                s={'direction':'BEARISH' if bear else 'BULLISH','entry':101 if bear else 99,'tick_size':.01}
                first=context.broad_reversal_reclaim(bars,s,approach,context.timeframe_profile('2H'))
                self.assertFalse(first['ok'])
                self.assertEqual(first['reason'],'BROAD_REVERSAL_NOT_RECLAIMED')
                s['entry']=99.99 if bear else 100.01
                self.assertTrue(context.broad_reversal_reclaim(bars,s,approach,context.timeframe_profile('2H'))['ok'])

    def test_all_daily_weekly_monthly_profiles_keep_existing_entry_rules(self):
        rows=[bar(0),bar(1000),bar(2000)]
        s={'direction':'BULLISH','entry':99,'tick_size':.01}
        for tf in bot.TIMEFRAMES:
            q=context.broad_reversal_reclaim(rows,s,{'approach_mode':'BROADER_REVERSAL'},context.timeframe_profile(tf))
            self.assertEqual(q['applies'],tf.endswith('H'))
            if not tf.endswith('H'):self.assertTrue(q['ok'])

    def test_pullback_and_c1_routes_do_not_inherit_broad_reversal_gate(self):
        for route in ('C1_C2_PULLBACK','C1_IMPULSE_APPROACH','C1_COMPLETES_APPROACH'):
            q=context.broad_reversal_reclaim([bar(0),bar(1000)],
                {'direction':'BULLISH','entry':99,'tick_size':.01},
                {'approach_mode':route},context.timeframe_profile('2H'))
            self.assertTrue(q['ok']);self.assertFalse(q['applies'])

    def test_live_c3_extrema_and_final_colour_cannot_create_the_trigger(self):
        rows=[bar(0),bot.Candle(1000,102,103,98,98,1,1999),bar(2000,130,90,125)]
        s={'direction':'BULLISH','entry':99,'tick_size':.01}
        q=context.broad_reversal_reclaim(rows,s,{'approach_mode':'BROADER_REVERSAL'},context.timeframe_profile('2H'))
        self.assertFalse(q['ok'])

    def test_new_production_config_enables_observation_and_preserves_watchlists(self):
        with patch.dict(os.environ,{'V36_RECLAIM_OBSERVE_ENABLED':'true',
                                    'TELEGRAM_CHAT_ID':'unit-test', 'TELEGRAM_BOT_TOKEN':'unit-test'}):
            self.assertTrue(bot.Config().reclaim_observation_enabled)
        self.assertIn('BTC',bot.SPOT_WATCHLIST);self.assertIn('QCOM',bot.FUTURES_WATCHLIST)

    def test_observation_never_vetoes_spot_or_stock_perps_and_higher_is_exempt(self):
        helper=legacy.QualityIntegration()
        for cls in (bot.Scanner,FuturesScanner):
            with tempfile.TemporaryDirectory() as folder:
                s=helper.scanner(folder)
                if cls is FuturesScanner:s=cls(s.cfg)
                s.cfg.reclaim_observation_enabled=True;w=helper.qualifying_watch()
                helper.seed_btc(s)
                setup=bot.make_setup(w,.1,True)
                q=s.quality_check(w,setup,w.last_ms)
                self.assertTrue(q['ok'],q)
                self.assertTrue(q['reversal_reclaim']['would_hold'])
                self.assertEqual(q['reversal_reclaim']['mode'],'OBSERVE_ONLY')
                w.timeframe='1D'
                q=s.quality_check(w,dict(setup,timeframe='1D'),w.last_ms)
                self.assertTrue(q['ok'],q);self.assertFalse(q['reversal_reclaim']['applies'])


class RangeRecovery(unittest.TestCase):
    def test_one_barrier_is_recoverable_in_both_directions(self):
        for bear in (False,True):
            r=record(bear=bear)
            bars=[bar(0,101,97.5)] if not bear else [bar(0,102.5,99)]
            self.assertTrue(outcomes.recover_ranges(r,bars,1000))
            self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'STOP')
            self.assertEqual(r['trade_tracking']['paths']['sl2']['tp1'],'UNKNOWN')
            self.assertEqual(outcomes.summary(r)['gap_reason'],'STREAM_RECONNECT')

    def test_targets_then_later_stop_keep_recovered_target(self):
        r=record()
        self.assertTrue(outcomes.recover_ranges(r,[bar(0,103,99)],1000))
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'TP')
        outcomes.recover_ranges(r,[bar(0,103,99),bar(1000,101,96)],2000)
        self.assertEqual(r['trade_tracking']['paths']['sl1'],{'tp1':'TP','tp2':'STOP'})

    def test_both_barriers_in_one_candle_stay_unknown_despite_later_target(self):
        r=record()
        outcomes.recover_ranges(r,[bar(0,103,97),bar(1000,105,99)],2000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')

    def test_pre_entry_target_cannot_become_a_recovered_win(self):
        r=record();r['candles'][0]['high']=103
        outcomes.recover_ranges(r,[bar(0,103,99)],1000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')

    def test_finer_ranges_resolve_a_coarse_ambiguity_without_invented_order(self):
        r=record()
        outcomes.recover_ranges(r,[bar(0,103,97)],1000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')
        minutes=[bar(0,101,99,duration=250),bar(250,103,99,duration=250),
                 bar(500,101,97,duration=250),bar(750,101,99,duration=250)]
        outcomes.recover_ranges(r,minutes,1000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'TP')

    def test_missing_cursor_bar_or_history_gap_prevents_recovery(self):
        for bars in ([bar(1000,103,99)], [bar(0),bar(2000,103,99)]):
            r=record();outcomes.recover_ranges(r,bars,3000)
            self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')

    def test_future_and_post_deadline_bars_cannot_credit_a_target(self):
        r=record();outcomes.recover_ranges(r,[bar(0,103,99)],999)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')
        outcomes.recover_ranges(r,[bar(3000,105,99)],4000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')

    def test_progress_is_persistent_and_long_horizon_can_expire_honestly(self):
        r=record();outcomes.recover_ranges(r,[bar(0)],1000)
        self.assertEqual(outcomes.recovery_cursor(r),1000)
        reloaded=deepcopy(r)
        outcomes.recover_ranges(reloaded,[bar(1000),bar(2000)],3000)
        self.assertEqual(reloaded['trade_tracking']['paths']['sl1']['tp1'],'EXPIRED')
        self.assertEqual(reloaded['trade_tracking']['recoveries'][-1]['deadline_close_price'],100)
        self.assertIsNone(outcomes.recovery_cursor(reloaded))

    def test_restart_during_weekly_path_recovers_from_gap_not_weekly_entry(self):
        r=record(base=0,duration=604800000)
        r['setup']['timeframe']='1W'
        r['trade_tracking']['last_ms']=10*3600000+1234
        # A narrow recovery window suffices even though entry was many hours ago.
        outcomes.recover_ranges(r,[bar(10*3600000,103,99,duration=3600000)],11*3600000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'TP')

    def test_existing_observed_verdict_and_frozen_levels_never_change(self):
        r=record();r['trade_tracking']['paths']['sl1']['tp1']='STOP'
        frozen=deepcopy(r['setup'])
        outcomes.recover_ranges(r,[bar(0,105,99)],1000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'STOP')
        self.assertEqual(r['setup'],frozen)
        for proof in r['trade_tracking']['recoveries']:
            self.assertNotIn('timestamp',proof)

    def test_malformed_candle_is_rejected(self):
        r=record();outcomes.recover_ranges(r,[bar(0,float('nan'),99)],1000)
        self.assertEqual(r['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')


class LiveIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_valid_btc_price_move_during_chart_refreshes_entry_and_keeps_levels(self):
        with tempfile.TemporaryDirectory() as folder:
            s=btc_scanner(folder);s.cfg.reclaim_observation_enabled=True;s.cfg.send_charts=True
            w,btc=recorded_watch();seed(s,w,btc)
            old=bot.make_setup(w,.1,True);rendered=[];captions=[]
            async def render(function,bars,setup):
                rendered.append(deepcopy(setup))
                w.observe(w.last_price-.04,w.last_ms+100)
                return b'png'
            async def photo(payload,png):
                captions.append(payload['caption']);return {'ok':True,'result':{'message_id':123}}
            s.telegram_photo=photo
            with patch.object(bot.time,'time',return_value=(w.last_ms+100)/1000),patch.object(bot.asyncio,'to_thread',side_effect=render):
                await s.send_watch(w)
            self.assertEqual(len(captions),1)
            r=s.store.data['alerts'][w.key]
            self.assertAlmostEqual(r['setup']['entry'],old['entry']-.04)
            self.assertIn('Entry: '+bot.infra.fmt_price(old['entry']-.04),captions[0])
            for level in ('sl1','sl2','tp1','tp2'):
                self.assertEqual(r['setup'][level],rendered[0][level])
            self.assertEqual(r['trade_tracking']['origin_ms'],w.last_ms)

    async def test_recorded_btc_missing_level_still_delivers_with_new_gate(self):
        for bull in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                s=btc_scanner(folder);s.cfg.reclaim_observation_enabled=True
                w,btc=recorded_watch(bull);seed(s,w,btc);calls=[]
                async def telegram(method,payload):
                    calls.append(method);return {'ok':True,'result':{'message_id':123}}
                s.telegram_call=telegram
                with patch.object(bot.time,'time',return_value=w.last_ms/1000):await s.send_watch(w)
                self.assertEqual(calls,['sendMessage'])
                r=s.store.data['alerts'][w.key]
                self.assertFalse(r['entry_quality']['location']['ok'])
                self.assertEqual(r['trade_tracking']['origin_ms'],r['observed_at_ms'])
                self.assertEqual(r['candles'][-1],asdict(w.bars[-1]))

    async def test_weak_reclaim_observation_does_not_delay_or_suppress_existing_alert(self):
        helper=legacy.QualityIntegration()
        for bear in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                s=helper.scanner(folder);s.cfg.reclaim_observation_enabled=True
                s.stream_connected=True;w=helper.qualifying_watch(bear);helper.seed_btc(s)
                calls=[]
                async def telegram(method,payload):
                    calls.append(method);return {'ok':True,'result':{'message_id':123}}
                s.telegram_call=telegram
                with patch.object(bot.time,'time',return_value=w.last_ms/1000):await s.send_watch(w)
                self.assertEqual(calls,['sendMessage'])
                self.assertTrue(s.store.data['alerts'][w.key]['entry_quality']['reversal_reclaim']['would_hold'])

    async def test_recovery_worker_is_bounded_and_does_not_read_other_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            s=legacy.QualityIntegration().scanner(folder);s.stream_connected=True
            for i in range(3):s.store.data['alerts'][str(i)]=record(3600000,3600000)
            other=record(3600000,3600000);other['market']='BINANCE_FUTURES'
            s.store.data['alerts']['other-feed']=other
            calls=[]
            async def fetch(symbol,interval,start,end):
                calls.append((interval,start,end));return [bar(3600000,105,99,duration=3600000)]
            async def stop(_):s.stop_event.set()
            s.fetch_span=fetch
            with patch.object(bot.time,'time',return_value=10801),patch.object(bot.asyncio,'sleep',side_effect=stop):
                await s.outcome_recovery_loop()
            self.assertEqual(len(calls),2)
            self.assertEqual(s.store.data['alerts']['0']['trade_tracking']['paths']['sl1']['tp1'],'TP')
            self.assertEqual(s.store.data['alerts']['2']['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')
            self.assertEqual(other['trade_tracking']['paths']['sl1']['tp1'],'UNKNOWN')
            self.assertIn('0',s.dirty_outcomes)

    async def test_performance_command_requires_existing_recipient(self):
        with tempfile.TemporaryDirectory() as folder:
            s=legacy.QualityIntegration().scanner(folder);calls=[]
            async def telegram(method,payload):calls.append(payload)
            s.telegram_call=telegram
            await s.handle_message({'chat':{'id':'stranger'},'text':'/performance'})
            self.assertEqual(calls,[])
            await s.handle_message({'chat':{'id':s.chat_id},'text':'/performance current'})
            self.assertEqual(len(calls),1);self.assertIn(bot.VERSION,calls[0]['text'])


class TimeframeScorecards(unittest.TestCase):
    def test_focus_markets_and_three_hour_plus_are_prioritized_without_dropping_others(self):
        with tempfile.TemporaryDirectory() as folder:
            s=legacy.QualityIntegration().scanner(folder)
            candidates=[('TAOUSDT','3H'),('BTCUSDT','2H'),('ZECUSDT','3H'),('HYPEUSDT','1D'),('BTCUSDT','3H')]
            ordered=sorted(candidates,key=lambda p:s.delivery_priority(*p))
            self.assertEqual(ordered[0],('BTCUSDT','3H'))
            self.assertLess(ordered.index(('ZECUSDT','3H')),ordered.index(('BTCUSDT','2H')))
            self.assertEqual(set(ordered),set(candidates))
            f=FuturesScanner(s.cfg)
            self.assertLess(f.delivery_priority('QCOMUSDT','3H'),f.delivery_priority('XAUUSDT','3H'))

    def test_timeframes_feeds_stop_paths_and_unknowns_are_separate(self):
        rows=[]
        for tf,market,state in [('2H','BINANCE_SPOT','STOP'),('1D','BINANCE_SPOT','TP'),
                                ('1W','BINANCE_FUTURES','UNKNOWN')]:
            r=record();r['setup']['timeframe']=tf;r['market']=market
            r['trade_tracking']['paths']['sl1']['tp1']=state;rows.append(r)
        text=outcomes.performance_text(rows)
        for value in ('Spot · Intraday','Spot · Daily','Perps · Weekly/monthly','unknown 1','stop 1','TP 1'):
            self.assertIn(value,text)
        self.assertIn('excludes unknown/expired/open',text)

    def test_pending_delivery_and_other_version_are_not_current_performance(self):
        r=record();r['setup']['strategy_version']='3.5.4'
        p=record();p['status']='pending'
        text=outcomes.performance_text([r,p],bot.VERSION)
        self.assertIn('No tracked delivered signals',text)


if __name__=='__main__':unittest.main()
