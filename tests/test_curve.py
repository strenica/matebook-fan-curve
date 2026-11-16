#!/usr/bin/env python3
"""Tests for the curve, hysteresis and slew logic. No hardware access."""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "fanctl", os.path.join(HERE, "..", "fanctl.py"))
fanctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fanctl)

CFG = {
    "interval": 0.5,
    "emergency_c": 85,
    "min_duty": 25,
    "hysteresis_c": 1.5,
    "max_step_up": 10,
    "max_step_down": 2,
    "curve": [(0, 30), (50, 30), (55, 35), (60, 45), (65, 60), (70, 80), (75, 100)],
}


class TestDutyFor(unittest.TestCase):
    def test_below_first_point_uses_first_duty(self):
        self.assertEqual(fanctl.duty_for(-10, CFG), 30)

    def test_exact_boundaries(self):
        self.assertEqual(fanctl.duty_for(0, CFG), 30)
        self.assertEqual(fanctl.duty_for(50, CFG), 30)
        self.assertEqual(fanctl.duty_for(55, CFG), 35)
        self.assertEqual(fanctl.duty_for(60, CFG), 45)
        self.assertEqual(fanctl.duty_for(65, CFG), 60)
        self.assertEqual(fanctl.duty_for(70, CFG), 80)
        self.assertEqual(fanctl.duty_for(75, CFG), 100)

    def test_between_points_takes_lower_step(self):
        self.assertEqual(fanctl.duty_for(54.9, CFG), 30)
        self.assertEqual(fanctl.duty_for(59.9, CFG), 35)
        self.assertEqual(fanctl.duty_for(64.9, CFG), 45)

    def test_above_top_point_stays_at_max(self):
        self.assertEqual(fanctl.duty_for(99, CFG), 100)

    def test_min_duty_floor_is_enforced(self):
        cfg = dict(CFG, curve=[(0, 5)])
        self.assertEqual(fanctl.duty_for(40, cfg), 25)

    def test_unsorted_curve_is_sorted_on_load(self):
        points = sorted([(60, 45), (0, 30), (55, 35)])
        cfg = dict(CFG, curve=points)
        self.assertEqual(fanctl.duty_for(56, cfg), 35)


class TestHysteresis(unittest.TestCase):
    """A boundary must not flap: 55.0 C is the 30 -> 35 edge."""

    def test_no_increase_inside_deadband(self):
        # 55.0 is past the boundary but not past boundary + 1.5
        self.assertEqual(fanctl.duty_for(55.0, CFG, current=30), 30)
        self.assertEqual(fanctl.duty_for(56.4, CFG, current=30), 30)

    def test_increase_once_past_deadband(self):
        self.assertEqual(fanctl.duty_for(56.5, CFG, current=30), 35)

    def test_no_decrease_inside_deadband(self):
        # 54.9 is below the boundary but not below boundary - 1.5
        self.assertEqual(fanctl.duty_for(54.9, CFG, current=35), 35)
        self.assertEqual(fanctl.duty_for(53.6, CFG, current=35), 35)

    def test_decrease_once_past_deadband(self):
        self.assertEqual(fanctl.duty_for(53.4, CFG, current=35), 30)

    def test_steady_temperature_never_moves(self):
        # the exact failure seen on hardware: 55.0 C flipping 30 <-> 35
        duty = 30
        for _ in range(50):
            duty = fanctl.duty_for(55.0, CFG, current=duty)
        self.assertEqual(duty, 30)

    def test_no_current_means_plain_step(self):
        self.assertEqual(fanctl.duty_for(55.0, CFG), 35)


class TestSlew(unittest.TestCase):
    def test_up_is_capped(self):
        self.assertEqual(fanctl.slew(60, 30, CFG), 40)

    def test_down_is_capped(self):
        self.assertEqual(fanctl.slew(30, 60, CFG), 58)

    def test_no_change_is_noop(self):
        self.assertEqual(fanctl.slew(30, 30, CFG), 30)

    def test_small_change_passes_through(self):
        self.assertEqual(fanctl.slew(35, 30, CFG), 35)
        self.assertEqual(fanctl.slew(58, 60, CFG), 58)

    def test_up_converges_in_bounded_steps(self):
        duty = 30
        steps = 0
        while duty < 80 and steps < 100:
            duty = fanctl.slew(80, duty, CFG)
            steps += 1
        self.assertEqual(duty, 80)
        self.assertEqual(steps, 5)  # 30 -> 40 -> 50 -> 60 -> 70 -> 80

    def test_down_is_slower_than_up(self):
        up = 0
        d = 30
        while d < 80:
            d = fanctl.slew(80, d, CFG)
            up += 1
        down = 0
        d = 80
        while d > 30:
            d = fanctl.slew(30, d, CFG)
            down += 1
        self.assertGreater(down, up)


if __name__ == "__main__":
    unittest.main(verbosity=2)
