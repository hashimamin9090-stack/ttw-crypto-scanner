"""BTC C1 approach regressions; exact candles do not prove live alert timing."""
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

import ttw_v3 as bot
from futures_feed import FuturesScanner
from test_ttw_v3 import candle
from test_ttw_v33 import history
from test_ttw_v350 import config
from test_ttw_v351 import link_candles


def btc_candles():
    # Binance Spot 4H OHLC, 14 Oct 20:00 through 15 Oct 20:00 UTC.
    # The last bar is completed OHLC, not an alleged historical entry snapshot.
    rows = [
        (1792008000000, 81840.95, 81980.10, 81444.42, 81682.01),
        (1792022400000, 81682.02, 81938.96, 80226.35, 80928.00),
        (1792036800000, 80928.00, 81743.61, 80500.01, 81541.34),
        (1792051200000, 81541.33, 81585.00, 80851.13, 80980.01),
        (1792065600000, 80980.01, 82631.00, 79800.00, 82418.00),
        (1792080000000, 82418.00, 82634.30, 81943.84, 82384.73),
        (1792094400000, 82384.74, 82588.01, 81942.24, 82245.93),
    ]
    return [candle(*row, duration=14400000) for row in rows]


def reflect(rows, total=200):
    # Synthetic opposite-direction cases, not additional observed BTC candles.
    return [replace(b, open=total-b.open, high=total-b.low,
                    low=total-b.high, close=total-b.close) for b in rows]


def synthetic_rows(bull=False):
    duration = 14400000
    prior = [candle((i-24)*duration, 101, 104, 98, 101, duration) for i in range(24)]
    prior[-3:] = [candle(-3*duration, 101, 101.3, 100.5, 100.8, duration),
                  candle(-2*duration, 100.8, 101.1, 100.2, 100.5, duration),
                  candle(-duration, 100.5, 100.8, 99.8, 100, duration)]
    anchors = [candle(0, 100, 104.1, 99.5, 103.5, duration),
               candle(duration, 103.5, 104, 102, 103.4, duration),
               candle(2*duration, 103.4, 103.9, 103.2, 103.2, duration)]
    rows = prior+anchors
    return reflect(rows) if bull else rows


def scanner(folder, cls=bot.Scanner):
    cfg = config(folder)
    cfg.impulse_range_fraction = .5
    cfg.rejection_wick_fraction = .1
    cfg.max_open_gap_range = 1.5
    result = cls(cfg)
    result.stream_connected = True
    result.tick_sizes['BTCUSDT'] = .01
    return result


def live_btc_watch(bull=False):
    rows = synthetic_rows(bull)
    side = 'BULLISH' if bull else 'BEARISH'
    plan = bot.make_plan(rows[-3:], side, .01, .25, .15)
    c3 = rows[-1]
    rows[-1] = replace(c3, high=c3.open, low=c3.open, close=c3.open)
    key = f'BTCUSDT|4H|{side}|{c3.open_time}'
    w = bot.Watch(key, 'BTCUSDT', '4H', rows, plan)
    w.verified_ms = c3.open_time
    w.observe(c3.low if bull else c3.high, c3.open_time+1000)
    w.observe(c3.close, c3.open_time+4000)
    return w


class BTCApproach(unittest.TestCase):
    def test_exact_candles_reproduce_recorded_rejection_and_new_approach(self):
        rows = btc_candles()
        before = bot.reversal_context(rows, 'BEARISH', .01)
        self.assertEqual(before['reason'], 'NO_OPPOSING_APPROACH')
        self.assertAlmostEqual(before['approach_net'], -702.01)
        self.assertAlmostEqual(before['anchor_net'], 1404.72)
        for side, bars in (('BEARISH', rows), ('BULLISH', reflect(rows, 165000))):
            for floor in (0, .25):
                after = bot.reversal_context(bars, side, .01, floor, allow_c1_impulse=True)
                self.assertTrue(after['ok'], after)
                self.assertEqual(after['approach_mode'], 'C1_IMPULSE_APPROACH')
                self.assertIsNotNone(bot.geometry(bars[-3:], side, .01, .25, .15))
                self.assertIsNotNone(bot.make_plan(bars[-3:], side, .01, .25, .15))

    def test_route_is_scoped_to_enabled_spot_btc_intraday(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            for tf in ('2H', '3H', '4H', '20H'):
                r = s.approach_context('BTCUSDT', tf, btc_candles(), 'BEARISH', .01)
                self.assertTrue(r['ok'])
                self.assertEqual(r['approach_policy'], 'BTC_INTRADAY')
            for symbol, tf in [('ETHUSDT', '4H'), ('NEARUSDT', '4H'),
                               ('BTCUSDT', '1D'), ('BTCUSDT', '1W'), ('BTCUSDT', 'badH')]:
                self.assertFalse(s.approach_context(symbol, tf, btc_candles(), 'BEARISH', .01)['ok'])
            f = scanner(folder, FuturesScanner)
            self.assertFalse(f.approach_context('BTCUSDT', '4H', btc_candles(), 'BEARISH', .01)['ok'])
            s.cfg.quality_enabled = False
            self.assertFalse(s.approach_context('BTCUSDT', '4H', btc_candles(), 'BEARISH', .01)['ok'])

    def test_small_body_doji_and_gap_do_not_supply_approach(self):
        for body in (0, .01, 1):
            rows = btc_candles()
            rows[-3] = replace(rows[-3], open=rows[-3].close-body)
            r = bot.reversal_context(rows, 'BEARISH', .01, allow_c1_impulse=True)
            self.assertFalse(r['ok'], r)
            self.assertFalse(r['c1_impulse']['ok'])

    def test_close_must_extend_prior_structure_by_at_least_one_tick(self):
        rows = btc_candles()
        extreme = max(b.high for b in rows[-6:-3])
        for extension, expected in ((-1, False), (0, False), (.005, False), (.01, True)):
            changed = rows.copy()
            changed[-3] = replace(changed[-3], close=extreme+extension)
            r = bot.reversal_context(changed, 'BEARISH', .01, allow_c1_impulse=True)
            self.assertEqual(r['ok'], expected, r)

    def test_c2_and_c3_cannot_manufacture_or_inflate_c1_evidence(self):
        for strong in (False, True):
            rows = btc_candles()
            if not strong:
                rows[-3] = replace(rows[-3], open=rows[-3].close-1)
            before = bot.reversal_context(rows, 'BEARISH', .01, .25, allow_c1_impulse=True)
            for i in (-2, -1):
                changed = rows.copy()
                changed[i] = replace(changed[i], high=1000000, low=1)
                after = bot.reversal_context(changed, 'BEARISH', .01, .25, allow_c1_impulse=True)
                self.assertEqual(after['ok'], before['ok'])
                self.assertEqual(after['c1_impulse'], before['c1_impulse'])

    def test_later_reversal_colour_c1_still_cannot_restart_the_reversal(self):
        r = bot.reversal_context(link_candles(), 'BULLISH', .001, allow_c1_impulse=True)
        self.assertEqual(r['reason'], 'C1_REVERSAL_EXTREME_MISSING')

    def test_c1_strength_floor_cannot_be_bypassed_by_structure_extension(self):
        rows = btc_candles()
        floor = bot.reversal_context(rows, 'BEARISH', .01, allow_c1_impulse=True)['c1_impulse']['minimum_body']
        for change, expected in ((-.01, False), (0, True), (.01, True)):
            changed = rows.copy()
            changed[-3] = replace(changed[-3], open=changed[-3].close-floor-change)
            r = bot.reversal_context(changed, 'BEARISH', .01, allow_c1_impulse=True)
            self.assertEqual(r['ok'], expected, r)

    def test_recorded_c3_impulse_before_touch_is_still_rejected(self):
        plan = bot.make_plan(btc_candles()[-3:], 'BEARISH', .01, .25, .15)
        adapted = bot.infra.TouchPlan(plan.direction, plan.contact, plan.upper-plan.lower,
                                      plan.impulse_distance, plan.candle3_open)
        # Exact threshold-crossing second and the maximum observed earlier
        # in C3. The minute history never reaches the touch zone before this.
        second = candle(1792097632000, 82103.05, 82103.05, 82094, 82097, 1000)
        event, _ = bot.infra.second_event(second, adapted, 82442.99)
        self.assertEqual(event['reason'], 'IMPULSE_BEFORE_TOUCH')
        # First possible contact is in the 22:01 UTC minute, after the impulse.
        touch_minute = candle(1792101660000, 82478.01, 82542.94, 82478.01, 82542.93, 60000)
        self.assertLess(82495.49, plan.lower)  # Maximum before that minute.
        self.assertGreaterEqual(touch_minute.high, plan.lower)
        self.assertLess(second.close_time, touch_minute.open_time)

    def test_missing_or_gapped_pre_c1_history_does_not_qualify(self):
        rows = btc_candles()
        self.assertFalse(bot.reversal_context(rows[-5:], 'BEARISH', .01, allow_c1_impulse=True)['ok'])
        rows[-4] = replace(rows[-4], close_time=rows[-4].close_time-1000)
        self.assertEqual(bot.reversal_context(rows, 'BEARISH', .01, allow_c1_impulse=True)['reason'],
                         'APPROACH_DATA_INVALID')


class BTCDelivery(unittest.IsolatedAsyncioTestCase):
    def seed_context(self, s, w):
        now = w.last_ms
        start = now//900000*900000
        price = w.last_price
        short = [replace(b, open=price, high=price+1, low=price-1, close=price)
                 for b in history(24, end=start)]
        short.append(candle(start, price, price+.2, price-.2, price, 900000))
        hours = [replace(b, open=price, high=price+1, low=price-1, close=price)
                 for b in history(24, 3600000, end=now//3600000*3600000)]
        s.btc_bars = {'15m':short, '1h':hours}
        s.last_quotes['BTCUSDT'] = (now, price)

    async def deliver(self, s, w):
        calls = []
        async def telegram(method, payload):
            calls.append(method)
            return {'ok':True, 'result':{'message_id':123}}
        s.telegram_call = telegram
        with patch.object(bot.time, 'time', return_value=w.last_ms/1000):
            await s.send_watch(w)
        return calls

    async def test_exact_candles_arm_before_touch_without_bypassing_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            rows = btc_candles()
            c3 = rows[-1]
            rows[-1] = replace(c3, high=c3.open, low=c3.open, close=c3.open)
            async def bases(symbol):
                return rows
            s.fetch_symbol_bases = bases
            s.candles_for_timeframe = lambda data, tf: data if tf == '4H' else []
            with patch.object(bot.time, 'time', return_value=(c3.open_time+1000)/1000):
                await s.scan_symbol('BTCUSDT')
            key = f'BTCUSDT|4H|BEARISH|{c3.open_time}'
            self.assertIn(key, s.watches, dict(s.stats))
            self.assertEqual(s.watches[key].verified_ms, 0)
            self.assertEqual(s.watches[key].touch_ms, 0)
            self.assertFalse(s.watches[key].ready(c3.open_time+1000, .4))
            self.assertFalse(s.store.data['alerts'])

    async def test_new_route_delivers_with_real_quality_both_directions_once(self):
        for bull in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                s = scanner(folder)
                w = live_btc_watch(bull)
                self.seed_context(s, w)
                self.assertTrue(w.ready(w.last_ms, .4))
                self.assertEqual(len(await self.deliver(s, w)), 1, dict(s.stats))
                record = s.store.data['alerts'][w.key]
                self.assertEqual(record['reversal_context']['approach_mode'], 'C1_IMPULSE_APPROACH')
                self.assertTrue(record['entry_quality']['location']['ok'])
                self.assertIn('risk_checks', record['entry_quality'])
                self.assertEqual(await self.deliver(s, w), [])

    async def test_btc_intraday_can_deliver_without_established_location(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            w = live_btc_watch()
            w.bars = w.bars[-6:]
            self.seed_context(s, w)
            self.assertEqual(len(await self.deliver(s, w)), 1)
            record = s.store.data['alerts'][w.key]
            self.assertEqual(record['entry_quality']['location']['reason'], 'NO_ESTABLISHED_LEVEL')
            self.assertFalse(record['entry_quality']['profile']['require_location'])
            self.assertTrue(w.alerted)

    async def test_new_approach_still_checks_btc_expansion_and_both_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            s = scanner(folder)
            w = live_btc_watch()
            self.seed_context(s, w)
            setup = bot.make_setup(w, .1, c2_reference=True)
            with patch.object(bot, 'btc_context', return_value={'ok':True, 'regime':'UP_EXPANSION'}):
                self.assertEqual(s.quality_check(w, setup, w.last_ms)['reason'], 'BTC_OPPOSING_EXPANSION')
            bad_stop = dict(setup, sl2=120)
            self.assertEqual(s.quality_check(w, bad_stop, w.last_ms)['reason'], 'C2_TARGET_TOO_SMALL_FOR_RISK')

    async def test_new_approach_still_needs_live_colour_and_early_entry(self):
        for bull in (False, True):
            w = live_btc_watch(bull)
            opening = w.plan.candle3_open
            w.observe(opening+(-.01 if bull else .01), w.last_ms+1)
            self.assertFalse(w.ready(w.last_ms, .4))
            w.observe(opening+(1.01 if bull else -1.01)*w.plan.impulse_distance, w.last_ms+1)
            self.assertEqual(w.failed, 'IMPULSE_ALREADY_AFTER_TOUCH')

    async def test_stale_quote_or_changed_price_during_render_prevents_delivery(self):
        for during_render in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                s = scanner(folder)
                w = live_btc_watch()
                self.seed_context(s, w)
                if during_render:
                    s.cfg.send_charts = True
                    async def render(*args):
                        w.last_price += .03
                        return b'chart'
                    with patch.object(bot.asyncio, 'to_thread', side_effect=render):
                        self.assertEqual(await self.deliver(s, w), [])
                else:
                    quote = s.last_quotes['BTCUSDT']
                    s.last_quotes['BTCUSDT'] = (quote[0]-4000, quote[1])
                    self.assertEqual(await self.deliver(s, w), [])
                    self.assertEqual(w.quality_reason, 'BTC_DATA_UNAVAILABLE')
                self.assertFalse(w.alerted)


if __name__ == '__main__':
    unittest.main()
