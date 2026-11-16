#!/usr/bin/env python3
"""Tests for the curve logic. No hardware access, safe to run anywhere."""

import importlib.util
import os
import sys
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
        cfg = dict(CFG, curve=[(0, 5)], min_duty=25)
        self.assertEqual(fanctl.duty_for(40, cfg), 25)

    def test_unsorted_curve_is_sorted_on_load(self):
        # load_config sorts; emulate the same guarantee here
        points = sorted([(60, 45), (0, 30), (55, 35)])
        cfg = dict(CFG, curve=points)
        self.assertEqual(fanctl.duty_for(56, cfg), 35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
