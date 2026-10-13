"""Regressions for the recorded 13 October LINK 2H candle-selection error."""
from dataclasses import replace
import unittest

import ttw_v3 as bot
from ttw_context import timeframe_profile
from test_ttw_v3 import approach, candle, fixture, reciprocal


def link_candles():
    # Exact completed anchors and live C3 OHLC from the 17:36 UTC alert audit.
    # These bars establish eligibility, not historical intrabar alert timing.
    rows = [
        (1791856800000, 12.689, 12.978, 12.660, 12.943),
        (1791864000000, 12.943, 13.188, 12.912, 13.178),
        (1791871200000, 13.178, 13.193, 12.975, 13.068),
        (1791878400000, 13.068, 13.111, 12.897, 12.991),
        (1791885600000, 12.992, 13.001, 12.724, 12.840),
        (1791892800000, 12.839, 13.049, 12.769, 12.883),
        (1791900000000, 12.882, 13.098, 12.825, 12.899),
        (1791907200000, 12.900, 12.981, 12.883, 12.914),
    ]
    return [candle(*row, duration=7200000) for row in rows]


def cases(rows):
    return [('BULLISH', rows), ('BEARISH', list(map(reciprocal, rows)))]


class C1ApproachRegression(unittest.TestCase):
    def test_recorded_earlier_trio_passes_shape_and_causal_approach(self):
        for side, rows in cases(link_candles()[:-1]):
            for tf in ('2H', '1W'):
                with self.subTest(side=side, tf=tf):
                    p = timeframe_profile(tf)
                    self.assertIsNotNone(bot.geometry(rows[-3:], side, .001,
                        p['geometry_wick_fraction'], p['geometry_price_cap_pct']))
                    result = bot.reversal_context(rows, side, .001, p['approach_range_fraction'])
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['approach_mode'], 'C1_COMPLETES_APPROACH')

    def test_recorded_shifted_trio_cannot_restart_the_reversal(self):
        for side, rows in cases(link_candles()):
            with self.subTest(side=side):
                self.assertIsNotNone(bot.geometry(rows[-3:], side, .001, .25, .15))
                for strength in (0, .25):
                    result = bot.reversal_context(rows, side, .001, strength)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['reason'], 'C1_REVERSAL_EXTREME_MISSING')

    def test_genuine_reversal_colour_c1_still_allowed(self):
        rows = approach() + fixture()
        rows[-3] = replace(rows[-3], open=101, close=102)
        for side, bars in cases(rows):
            self.assertTrue(bot.reversal_context(bars, side, .001, .25)['ok'])

    def test_reversal_colour_c1_must_extend_all_prior_extremes(self):
        rows = approach() + fixture()
        rows[-3] = replace(rows[-3], open=101, close=102)
        rows[-6] = replace(rows[-6], low=99.9)
        for side, bars in cases(rows):
            self.assertEqual(bot.reversal_context(bars, side, .001)['reason'],
                             'C1_REVERSAL_EXTREME_MISSING')

    def test_same_tip_and_sub_tick_new_tip_are_insufficient(self):
        rows = approach() + fixture()
        rows[-3] = replace(rows[-3], open=102, close=103)
        for low in (rows[-4].low, rows[-4].low-.0005):
            bars = rows.copy()
            bars[-3] = replace(bars[-3], low=low)
            self.assertFalse(bot.reversal_context(bars, 'BULLISH', .001)['ok'])

    def test_c1_completed_run_requires_three_opposing_bodies(self):
        rows = link_candles()[:-1]
        for i in (-5, -4, -3):
            changed = rows.copy()
            changed[i] = replace(changed[i], close=changed[i].open)
            for side, bars in cases(changed):
                self.assertFalse(bot.reversal_context(bars, side, .001)['ok'])

    def test_c1_completed_run_requires_opposing_structure_steps(self):
        rows = link_candles()[:-1]
        for i in (-5, -4, -3):
            rows[i] = replace(rows[i], high=13.3, low=12.7)
        for side, bars in cases(rows):
            self.assertFalse(bot.reversal_context(bars, side, .001)['ok'])

    def test_c1_completed_run_retains_the_intraday_strength_floor(self):
        rows = [candle(i*7200000, 100+.006-i*.001, 101, 99-i*.001,
                       100+.005-i*.001, duration=7200000) for i in range(6)]
        # C2 turns; only the three completed opposing bodies through C1 qualify.
        rows[-2] = replace(rows[-2], close=rows[-2].open+.002)
        for side, bars in cases(rows):
            self.assertTrue(bot.reversal_context(bars, side, .0001)['ok'])
            r = bot.reversal_context(bars, side, .0001, .25)
            self.assertFalse(r['ok'])
            self.assertEqual(r['reason'], 'INTRADAY_APPROACH_TOO_SMALL')

    def test_c3_cannot_supply_missing_extreme_or_approach(self):
        for original in (link_candles(), link_candles()[:-1]):
            rows = original.copy()
            before = bot.reversal_context(rows, 'BULLISH', .001, .25)
            rows[-1] = replace(rows[-1], high=100, low=1, close=50)
            self.assertEqual(bot.reversal_context(rows, 'BULLISH', .001, .25), before)


if __name__ == '__main__':
    unittest.main()
