"""Тесты проверки golden set: без сети и без базы."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_golden import check  # noqa: E402


def feature(building_id: int, bonuses: dict, penalties: dict) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": building_id,
            "breakdown": {"base": 5.0, "index": 5.0,
                          "bonuses": bonuses, "penalties": penalties},
        },
        "geometry": {"type": "MultiPolygon", "coordinates": []},
    }


def bonus(value: float, distance: float) -> dict:
    return {"bonus": value, "distance_m": distance, "minutes": 5.0, "multiplier": 1.0}


def penalty(value: float, distance: float) -> dict:
    return {"penalty": value, "distance_m": distance, "class": "road", "weight": 1.0}


def fact(building_id: int, factor: str, kind: str, expect: str,
         distance: float = 0.0, check_distance: bool = False,
         tolerance: float = 0.0, flag: str | None = None) -> dict:
    return {
        "building_id": building_id, "factor": factor, "type": kind,
        "expect": expect, "distance_m": distance, "tolerance_m": tolerance,
        "check_distance": check_distance, "source": "test", "address": None,
        "flag": flag,
    }


def feature_v2(building_id: int, factors: dict, flags: list[str],
               index: float = 5.0) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": building_id,
            "breakdown": {"version": 2, "index": index,
                          "v2": {"index": index, "factors": factors,
                                 "flags": flags}},
        },
        "geometry": {"type": "MultiPolygon", "coordinates": []},
    }


def factor(value: float, distance: float | None = None,
           status: str = "ok") -> dict:
    entry = {"value": value, "status": status, "weight": 40, "contribution": 0}
    if distance is not None:
        entry["distance_m"] = distance
    return entry


class TestGoldenCheck(unittest.TestCase):
    def run_check(self, facts, features):
        buildings = {f["properties"]["id"]: f["properties"] for f in features}
        return check(facts, buildings)

    def test_positive_and_zero_pass(self):
        features = [
            feature(1, {"transport": bonus(1.5, 148)}, {}),
            feature(2, {"transport": bonus(0.0, 1800)}, {}),
        ]
        report = self.run_check([
            fact(1, "transport", "bonus", "positive"),
            fact(2, "transport", "bonus", "zero"),
        ], features)
        self.assertEqual(report["critical"], 0)
        self.assertEqual(report["ok"], 2)

    def test_wrong_sign_is_critical(self):
        features = [feature(1, {"transport": bonus(0.0, 1800)}, {})]
        report = self.run_check(
            [fact(1, "transport", "bonus", "positive")], features)
        self.assertEqual(report["critical"], 1)

    def test_missing_building_is_critical(self):
        report = self.run_check(
            [fact(99, "transport", "bonus", "positive")], [])
        self.assertEqual(report["critical"], 1)

    def test_penalty_boundary(self):
        features = [
            feature(1, {}, {"road": penalty(-0.1, 198.0)}),
            feature(2, {}, {"road": penalty(0.0, 205.0)}),
        ]
        report = self.run_check([
            fact(1, "road", "penalty", "positive", 198.0, True, 5.0),
            fact(2, "road", "penalty", "zero", 205.0, True, 5.0),
        ], features)
        self.assertEqual(report["critical"], 0)

    def test_distance_drift_is_warning(self):
        features = [feature(1, {}, {"road": penalty(-0.5, 150.0)})]
        report = self.run_check(
            [fact(1, "road", "penalty", "positive", 200.0, True, 5.0)], features)
        self.assertEqual(report["critical"], 0)
        self.assertEqual(report["warnings"], 1)

    def test_v2_factor_positive_and_zero(self):
        features = [
            feature_v2(1, {"transport": factor(0.8, 40.0)}, []),
            feature_v2(2, {"transport": factor(0.0, 260.0)}, []),
        ]
        report = self.run_check([
            fact(1, "transport", "factor", "positive", 40.0, True, 5.0),
            fact(2, "transport", "factor", "zero", 260.0, True, 5.0),
        ], features)
        self.assertEqual(report["critical"], 0)
        self.assertEqual(report["ok"], 2)

    def test_v2_required_flag_missing_is_critical(self):
        features = [feature_v2(1, {"green": factor(0.0, 100.0,
                                                  status="not_classified")}, [])]
        report = self.run_check(
            [fact(1, "green", "factor", "zero", flag="green:not_classified")],
            features)
        self.assertEqual(report["critical"], 1)

    def test_v2_required_flag_present_is_ok(self):
        features = [feature_v2(1, {"home": factor(0.0, status="not_classified")},
                               ["home:not_classified"])]
        report = self.run_check(
            [fact(1, "home", "factor", "zero", flag="home:not_classified")],
            features)
        self.assertEqual(report["critical"], 0)
        self.assertEqual(report["ok"], 1)


if __name__ == "__main__":
    unittest.main()
