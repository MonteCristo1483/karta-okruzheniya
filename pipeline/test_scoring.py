"""Юнит-тесты скоринга (T11). Коэффициенты — из config/scoring.json.

Запуск из корня репозитория:
  pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v
"""

from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import (  # noqa: E402
    bonus_for_distance,
    dec,
    finalize_index,
    penalty_for_distance,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def minutes_to_distance(minutes) -> Decimal:
    """Расстояние, дающее ровно столько минут пешком при скорости из конфига.

    Строится как точное обращение формулы walking_minutes:
    мин * (скорость * 1000 / 60), чтобы граница порога попадала ровно.
    """
    speed = dec(CFG["bonuses"]["walking_speed_kmh"])
    return dec(minutes) * (speed * 1000 / 60)


def expected_multiplier(minutes) -> Decimal:
    for t in sorted(CFG["bonuses"]["time_thresholds_min"], key=lambda x: x["up_to"]):
        if dec(minutes) <= dec(t["up_to"]):
            return dec(t["multiplier"])
    return Decimal(0)


class TestBonusBoundaries(unittest.TestCase):
    def check_minutes(self, minutes):
        category_max = dec(CFG["bonuses"]["categories"]["parks"]["max"])
        multiplier = expected_multiplier(minutes)
        result = bonus_for_distance(minutes_to_distance(minutes), category_max, CFG)
        self.assertEqual(result["multiplier"], float(multiplier))
        self.assertEqual(dec(result["bonus"]), category_max * multiplier)

    def test_exactly_5_min_full_bonus(self):
        self.check_minutes(5)

    def test_exactly_10_min_two_thirds(self):
        self.check_minutes(10)

    def test_exactly_15_min_one_third(self):
        self.check_minutes(15)

    def test_16_min_zero_bonus(self):
        self.check_minutes(16)


class TestPenaltyBoundaries(unittest.TestCase):
    def setUp(self):
        self.road = CFG["penalties"]["road"]
        self.trunk_weight = dec(self.road["classes"]["trunk"])

    def test_exactly_radius_zero_penalty(self):
        radius = dec(self.road["radius_m"])
        penalty = penalty_for_distance(
            radius, dec(self.road["max"]), self.trunk_weight, radius
        )
        self.assertEqual(penalty, 0)

    def test_zero_distance_full_penalty(self):
        penalty = penalty_for_distance(
            Decimal(0), dec(self.road["max"]), self.trunk_weight,
            dec(self.road["radius_m"]),
        )
        self.assertEqual(penalty, dec(self.road["max"]) * self.trunk_weight)


class TestIndexClamp(unittest.TestCase):
    def test_clamps_to_max(self):
        total = dec(CFG["index"]["max"]) + Decimal("0.4")
        self.assertEqual(finalize_index(total, CFG), dec(CFG["index"]["max"]))

    def test_clamps_to_min(self):
        total = dec(CFG["index"]["min"]) - Decimal("0.4")
        self.assertEqual(finalize_index(total, CFG), dec(CFG["index"]["min"]))

    def test_rounds_half_up(self):
        self.assertEqual(finalize_index(Decimal("7.25"), CFG), Decimal("7.3"))


if __name__ == "__main__":
    unittest.main()
