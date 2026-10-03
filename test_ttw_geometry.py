import unittest
from dataclasses import replace, asdict
import TTW_BOT_V2 as bot
from test_ttw_touch_first import candle, mirror
import test_ttw_touch_first as fixtures

class ActualGeometryTests(unittest.TestCase):
    def test_exact_alignment_both_directions(self):
        for bearish in (False,True):
            bars,_,plan=fixtures.EarlyRejectionRules().fixture(bearish)
            self.assertTrue(bot.actual_wick_geometry(bars,plan.direction))

    def test_near_style_offset_passed_old_checks_but_is_now_blocked(self):
        # Illustrative OHLC reconstruction from rounded screenshot prices;
        # not a claim to reproduce the unavailable original alert snapshot.
        bars=[candle(0,4.845,4.991,4.590,4.694),
              candle(1000,4.694,4.739,4.621,4.670),
              candle(2000,4.670,4.670,4.648,4.657)]
        for bearish in (False,True):
            current=list(map(mirror,bars)) if bearish else bars
            direction='BEARISH' if bearish else 'BULLISH'
            plan=bot.touch_plan(current,direction,.05,1,.5)
            self.assertIsNotNone(plan)
            tip=current[-1].high if bearish else current[-1].low
            self.assertLess(abs(tip-plan.level),plan.tolerance)
            self.assertFalse(bot.actual_wick_geometry(current,direction))

    def test_short_c3_wick_controls_allowance(self):
        bars,seconds,plan=fixtures.EarlyRejectionRules().fixture()
        bars[-1]=replace(bars[-1],low=99.59,close=99.75)
        self.assertFalse(bot.actual_wick_geometry(bars,plan.direction))
        self.assertFalse(bot.rejection_ready(bars,plan,seconds,7201000,7204000,.25))

    def test_small_offset_is_allowed(self):
        bars,_,plan=fixtures.EarlyRejectionRules().fixture()
        bars[-1]=replace(bars[-1],low=99.599)
        self.assertTrue(bot.actual_wick_geometry(bars,plan.direction))

    def test_confirmed_close_rechecks_actual_geometry(self):
        bars,_,plan=fixtures.EarlyRejectionRules().fixture()
        setup=bot.setup_at_touch('TESTUSDT','4H',bars,plan,{'touch_time':7201000},.1)
        bars[-1]=replace(bars[-1],low=99.55,high=100.3,close=100.2)
        result=bot.final_verdict(bars,asdict(setup),.05)
        self.assertIn('actual three-wick',result)

    def test_missing_wick_is_rejected(self):
        bars,_,plan=fixtures.EarlyRejectionRules().fixture()
        bars[-1]=replace(bars[-1],close=bars[-1].low)
        self.assertFalse(bot.actual_wick_geometry(bars,plan.direction))

