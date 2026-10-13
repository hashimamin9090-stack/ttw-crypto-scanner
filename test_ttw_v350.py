import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import TTW_BOT_V2 as infra
import ttw_context as context
import ttw_outcomes as outcomes
import ttw_v3 as bot
from futures_feed import FuturesScanner, trade_sequence
from market_io import MarketIO
from test_ttw_v3 import candle
import test_ttw_v33 as legacy


def config(folder):
    return SimpleNamespace(telegram_chat_id='private-test', max_concurrency=2,
        state_dir=folder, stop_buffer_pct=.1, send_charts=False,
        max_entry_move_fraction=.25, quality_enabled=True, precision_enabled=True,
        structure_targets=True, context_history=40, reclaim_fraction=.08,
        level_proximity=.35, min_target_r=1.5, min_room_r=1.25,
        symbol_override=list(bot.SPOT_WATCHLIST), futures_symbols=list(bot.FUTURES_WATCHLIST),
        stream_url='wss://stream.binance.com:443')


def instrument(base, **kwargs):
    return dict(symbol=base+'USDT', status='TRADING', quoteAsset='USDT',
        isSpotTradingAllowed=True, contractType='TRADIFI_PERPETUAL', onboardDate=0,
        filters=[dict(filterType='PRICE_FILTER', tickSize='.01')], **kwargs)


def row(a, timestamp, price, quantity=1):
    return dict(a=a, T=timestamp, p=price, q=quantity)


class Timeframes(unittest.TestCase):
    def test_requested_and_additional_timeframes_have_links_and_profiles(self):
        self.assertEqual(len(bot.TIMEFRAMES), 36)
        for tf in ('2W','3W','10D','15D','20D','25D','20H','8W','12M'):
            self.assertIn(tf, bot.TIMEFRAMES)
            self.assertIn(tf, infra.TV_INTERVAL)
            self.assertEqual(context.timeframe_profile(tf)['name'],
                             'INTRADAY' if tf.endswith('H') else 'HIGHER')
        self.assertEqual(infra.TV_INTERVAL['20H'], '1200')
        self.assertEqual(infra.TV_INTERVAL['3W'], '3W')

    def test_3_week_buckets_start_monday_and_preserve_exact_ohlc(self):
        start, end = infra.bucket_bounds(0, 3, 'week')
        self.assertEqual(datetime.fromtimestamp(start/1000, timezone.utc).weekday(), 0)
        source = [candle(start+i*604800000, 100, 105+i, 99-i, 101, 604800000) for i in range(3)]
        bars = infra.aggregate_candles(source, 3, 'week')
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0].open_time, bars[0].close_time), (start, end-1))
        self.assertEqual((bars[0].high, bars[0].low), (107,97))

    def test_large_day_buckets_do_not_pad_missing_history(self):
        for factor in (10,15,20,25):
            start, end = infra.bucket_bounds(0, factor, 'day')
            rows = [candle(start+i*86400000,100,101,99,100,86400000) for i in range(factor)]
            self.assertEqual(infra.aggregate_candles(rows,factor,'day')[0].close_time,end-1)
            self.assertEqual(infra.aggregate_candles(rows[1:],factor,'day'),[])
            self.assertEqual(infra.aggregate_candles(rows[:2]+rows[3:],factor,'day'),[])

    def test_new_parent_timeframes_are_larger_and_valid(self):
        self.assertEqual(context.PARENTS['20H'],'1D')
        self.assertEqual(context.PARENTS['20D'],'1M')
        self.assertEqual(context.PARENTS['8W'],'3M')
        self.assertNotIn('1M',context.PARENTS)  # Existing monthly policy is preserved.


class Routing(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_spot_list_never_adds_other_coins_or_moves_crypto_to_perps(self):
        with tempfile.TemporaryDirectory() as d:
            s=bot.Scanner(config(d)); requests=[]
            async def get(url,**kw):
                requests.append(url)
                return dict(symbols=[instrument(b) for b in bot.SPOT_WATCHLIST+('XRP','BNB')])
            s._get_json=get
            await s.refresh_universe()
            self.assertEqual(s.symbols,[b+'USDT' for b in bot.SPOT_WATCHLIST])
            self.assertTrue(requests[0].endswith('/api/v3/exchangeInfo'))
            self.assertEqual(s.asset_class('HYPEUSDT'),'CRYPTO')

    async def test_unavailable_spot_pair_is_reported_without_a_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            s=bot.Scanner(config(d))
            async def get(*a,**kw):return dict(symbols=[instrument('BTC')])
            s._get_json=get;await s.refresh_universe()
            self.assertEqual(s.symbols,['BTCUSDT'])
            self.assertIn('HYPEUSDT',s.unavailable_symbols)

    async def test_futures_contains_only_selected_equities_and_metals(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d));requests=[]
            async def get(url,**kw):
                requests.append(url)
                return dict(symbols=[instrument(b) for b in bot.FUTURES_WATCHLIST+('BTC','HYPE','GOOG')])
            s._get_json=get;await s.refresh_universe()
            self.assertEqual(s.symbols,[b+'USDT' for b in bot.FUTURES_WATCHLIST])
            self.assertNotIn('BTCUSDT',s.market_symbols())
            self.assertNotIn('HYPEUSDT',s.market_symbols())
            self.assertTrue(requests[0].endswith('/fapi/v1/exchangeInfo'))
            self.assertEqual(s.asset_class('XAGUSDT'),'METAL')
            self.assertEqual(s.asset_class('QCOMUSDT'),'EQUITY')

    async def test_futures_cannot_accept_crypto_or_removed_es_goog(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d))
            async def get(*a,**kw):return dict(symbols=[])
            s._get_json=get
            for b in ('BTC','HYPE','ES','GOOG'):
                s.cfg.futures_symbols=[b]
                with self.assertRaises(ValueError):await s.refresh_universe()

    async def test_unsupported_and_nontrading_contracts_are_not_silently_active(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d)); listing=instrument('QCOM')
            listing['status']='BREAK'
            async def get(*a,**kw):return dict(symbols=[listing])
            s._get_json=get;await s.refresh_universe()
            self.assertEqual(s.symbols,[])
            self.assertIn('QCOMUSDT',s.unavailable_symbols)

    async def test_first_incomplete_native_interval_is_not_a_pattern_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d));s.onboard_dates['QCOMUSDT']=1
            async def get(*a,**kw):return [[0,100,102,99,101,1,3599999],
                                         [3600000,101,103,100,102,1,7199999]]
            s._get_json=get
            bars=await s.fetch_klines('QCOMUSDT','1h')
            self.assertEqual([b.open_time for b in bars],[3600000])

    async def test_market_state_is_shared_but_outcome_feed_ownership_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config(d);store=infra.StateStore(d)
            w=legacy.QualityIntegration().qualifying_watch()
            setup=bot.make_setup(w,.1)
            for name,market in [('old','BINANCE_SPOT'),('new','BINANCE_FUTURES')]:
                s=dict(setup,symbol='QCOMUSDT')
                if name=='new':s['market']=market
                store.data['alerts'][name]=dict(setup=s,status='sent',
                    trade_tracking=outcomes.start_tracking(s,7204000,10))
            spot=bot.Scanner(cfg,store=store);future=FuturesScanner(cfg,store=store)
            self.assertIs(spot.store,future.store)
            self.assertEqual(spot.outcome_keys['QCOMUSDT'],{'old'})
            self.assertEqual(future.outcome_keys['QCOMUSDT'],{'new'})
            self.assertIsNot(spot.market_io.state,future.market_io.state)
            future.market_io.state.update(until=10**12,code=429)
            self.assertFalse(spot.market_io.cooling)
            self.assertTrue(future.market_io.cooling)

    async def test_perp_links_and_caption_identify_the_actual_contract(self):
        w=legacy.QualityIntegration().qualifying_watch(); s=bot.make_setup(w,.1)
        text,keyboard=bot.alert_payload(dict(s,symbol='QCOMUSDT',market='BINANCE_FUTURES'),w.key)
        self.assertIn('PERP',text)
        self.assertIn('QCOMUSDT.P',keyboard['inline_keyboard'][1][0]['url'])
        spot_text,spot_keys=bot.alert_payload(s,w.key)
        self.assertNotIn('PERP',spot_text)
        self.assertNotIn('.P&',spot_keys['inline_keyboard'][1][0]['url'])

    async def test_own_structure_passes_without_btc_but_crypto_still_requires_btc(self):
        helper=legacy.QualityIntegration()
        for symbol in ('QCOMUSDT','XAUUSDT','XAGUSDT'):
            for bear in (False,True):
                with tempfile.TemporaryDirectory() as d:
                    cfg=config(d); spot=bot.Scanner(cfg); future=FuturesScanner(cfg,store=spot.store)
                    w=helper.qualifying_watch(bear);w.symbol=symbol;w.parent_bars=[]
                    s=bot.make_setup(w,.1,c2_reference=True)
                    self.assertEqual(spot.quality_check(w,s,7204000)['reason'],'BTC_DATA_UNAVAILABLE')
                    q=future.quality_check(w,s,7204000)
                    self.assertTrue(q['ok'],q)
                    self.assertEqual(q['btc']['regime'],'NOT_APPLICABLE')
                    self.assertTrue(q['location']['ok'])

    async def test_futures_still_blocks_missing_structure_and_late_reclaims(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d));w=legacy.QualityIntegration().qualifying_watch();w.parent_bars=[]
            setup=bot.make_setup(w,.1,c2_reference=True)
            q=s.quality_check(w,setup,7204000)
            self.assertTrue(q['ok'],q)
            w.bars=w.bars[-6:]
            self.assertFalse(s.quality_check(w,setup,7204000)['ok'])


class Recovery(unittest.IsolatedAsyncioTestCase):
    async def test_full_futures_recovery_colour_flip_and_delivery_both_directions(self):
        for bear in (False,True):
            with tempfile.TemporaryDirectory() as d:
                s=FuturesScanner(config(d));w=legacy.QualityIntegration().qualifying_watch(bear)
                w.symbol='QCOMUSDT';w.key=w.key.replace('TESTUSDT','QCOMUSDT');w.parent_bars=[]
                s.symbols=[w.symbol];s.stream_connected=True;s.stream_epoch=1
                price=lambda p:10000/p if bear else p
                prices=[price(p) for p in (98.28,99.5,98.01,98.31,98.31)]
                trades=[row(i+1,t,p) for i,(t,p) in enumerate(zip(
                    (7200000,7200100,7201000,7202000,7203000),prices))]
                requests=[];messages=[]
                async def get(url,params):
                    requests.append((url,params))
                    if url.endswith('/aggTrades'):return trades
                    size=3600000 if params['interval']=='1h' else 60000
                    return [[7200000,prices[0],max(prices)+50,min(prices),prices[-1]+40,
                             10,7200000+size-1]]
                async def telegram(method,payload):
                    messages.append((method,payload));return dict(ok=True,result=dict(message_id=123))
                s._get_json=get;s.telegram_call=telegram
                s.buffers[w.symbol]=deque([(7204000,price(98.4))],maxlen=10000)
                s.symbol_last_id[w.symbol]=6
                with patch('ttw_v3.time.time',return_value=7204):
                    await s.verify_watch(w)
                    self.assertFalse(w.failed,w.failed)
                    self.assertTrue(w.ready(7204000,.4))
                    await s.send_watch(w)
                self.assertTrue(w.alerted,dict(s.stats))
                self.assertEqual(len(messages),1)
                saved=s.store.data['alerts'][w.key]
                self.assertEqual(saved['market'],'BINANCE_FUTURES')
                self.assertEqual(saved['setup']['asset_class'],'EQUITY')
                self.assertEqual(saved['entry_quality']['btc']['regime'],'NOT_APPLICABLE')
                self.assertTrue(all('/fapi/v1/' in r[0] for r in requests))
                self.assertFalse(any(p.get('interval')=='1s' for _,p in requests))

    async def test_touch_and_impulse_in_same_second_use_actual_order(self):
        plan=infra.TouchPlan('BULLISH',98,.1,2,100)
        first=[row(1,100,100),row(2,100,98),row(3,100,101)]
        last=[row(1,100,100),row(2,100,102.1),row(3,100,98)]
        event,_=trade_sequence(first,plan,100)
        self.assertEqual(event['reason'],'TOUCH_FIRST')
        self.assertEqual(event['touch_time'],100)
        self.assertEqual(trade_sequence(last,plan,100)[0]['reason'],'IMPULSE_BEFORE_TOUCH')
        mirror=[dict(r,p=200-r['p']) for r in first]
        self.assertEqual(trade_sequence(mirror,infra.TouchPlan('BEARISH',102,.1,2,100),100)[0]['reason'],'TOUCH_FIRST')

    async def test_trade_pagination_preserves_original_cutoff_and_id_order(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d));calls=[]
            async def get(url,params):
                calls.append((url,params))
                return ([row(i,100+i,100) for i in range(1,1001)] if len(calls)==1 else
                        [row(1001,1101,98),row(1002,2001,90)])
            s._get_json=get
            with patch('futures_feed.time.time',return_value=3):
                rows=await s.exact_trades('XAUUSDT',0,2000)
            self.assertEqual(len(rows),1001)
            self.assertEqual(rows[-1]['a'],1001)
            self.assertEqual(calls[1][1],dict(symbol='XAUUSDT',fromId=1001,limit=1000))
            self.assertTrue(all('/fapi/v1/aggTrades' in c[0] for c in calls))

    async def test_missing_reordered_duplicate_and_nonfinite_trades_defer(self):
        cases=[[row(1,1,100),row(3,2,98)], [row(1,2,100),row(2,1,98)],
               [row(1,1,100),row(1,2,98)], [row(1,1,float('nan'))]]
        for page in cases:
            with tempfile.TemporaryDirectory() as d:
                s=FuturesScanner(config(d))
                async def get(*a,**kw):return page
                s._get_json=get
                with patch('futures_feed.time.time',return_value=3):
                    with self.assertRaises(ValueError):await s.exact_trades('XAUUSDT',0,2000)

    async def test_old_trade_prefix_and_spot_second_endpoint_are_never_fabricated(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d))
            async def forbidden(*a,**kw):self.fail('Unsupported recovery made a request')
            s._get_json=forbidden
            with patch('futures_feed.time.time',return_value=200000):
                with self.assertRaises(ValueError):await s.exact_trades('QCOMUSDT',0,999)
            with self.assertRaises(ValueError):await s.fetch_span('QCOMUSDT','1s',0,999)

    async def test_partial_hour_ignores_price_moves_after_verified_cutoff(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d))
            minutes=[candle(0,100,500,10,500,60000)]
            async def fetch(*a):return minutes
            async def trades(*a):return [row(1,0,100),row(2,500,98),row(3,1500,99)]
            s.fetch_span=fetch;s.exact_trades=trades
            bars=await s.truncate_history('QCOMUSDT',[candle(0,100,500,10,500)],1999)
            self.assertEqual((bars[-1].high,bars[-1].low,bars[-1].close),(100,98,99))

    async def test_native_candle_mismatch_is_not_certified_by_partial_trades(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d))
            with self.assertRaises(ValueError):
                s.validate_trade_bar(candle(0,100,110,98,99,60000),
                                     [row(1,0,100),row(2,1,98),row(3,2,99)])

    async def test_event_minute_uses_trade_order_and_does_not_call_second_klines(self):
        with tempfile.TemporaryDirectory() as d:
            s=FuturesScanner(config(d));calls=[]
            async def fetch(*a):self.fail('Exact event minute requested candles')
            async def trades(symbol,start,end):
                calls.append((symbol,start,end))
                return [row(1,0,100),row(2,1,98),row(3,2,99)]
            s.fetch_span=fetch;s.exact_trades=trades
            p=infra.TouchPlan('BULLISH',98,.1,2,100)
            e,_=await s.walk_sequence('QCOMUSDT',[candle(0,100,100,98,99,60000)],p,59999,'1m')
            self.assertEqual(e['reason'],'TOUCH_FIRST')
            self.assertEqual(e['touch_time'],1)
            self.assertEqual(calls,[('QCOMUSDT',0,59999)])

    async def test_futures_rest_weight_accounts_for_trade_recovery_and_large_seeds(self):
        self.assertEqual(MarketIO.weight('https://fapi.binance.com/fapi/v1/aggTrades'),20)
        self.assertEqual(MarketIO.weight('https://fapi.binance.com/fapi/v1/klines',{'limit':1000}),5)
        self.assertEqual(MarketIO.weight('https://fapi.binance.com/fapi/v1/klines',{'limit':40}),1)
        self.assertEqual(MarketIO.weight('https://api.binance.com/api/v3/klines',{'limit':1000}),2)


if __name__ == '__main__':
    unittest.main()
