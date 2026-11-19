"""Инварианты формулы индекса (Эталон-2, шаг 3).

Проверяем не «правильную оценку района», а поведение формулы: границы,
монотонность, кламп, детерминизм. Все коэффициенты — из config/scoring.json;
тесты падают, если кто-то поменяет формулу и сломает эти свойства.

Инвариант «сдвиг всех объектов на +0.01° не меняет индекс» здесь не
проверяется: он требует spatial join и живёт в golden set (проверка данных),
а не в чистой формуле.

Запуск:
  pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v
"""

from __future__ import annotations

import json
import math
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
    walking_minutes,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
SPEED = dec(CFG["bonuses"]["walking_speed_kmh"])


def distance_for_minutes(minutes) -> Decimal:
    return dec(minutes) * (SPEED * 1000 / 60)


def max_weight(penalty_cfg: dict) -> Decimal:
    classes = penalty_cfg.get("classes")
    if isinstance(classes, dict):
        return max(dec(weight) for weight in classes.values())
    return Decimal(1)


class TestBonusMonotonicity(unittest.TestCase):
    def test_bonus_never_grows_with_distance(self):
        for name, cfg in CFG["bonuses"]["categories"].items():
            previous = None
            for meters in range(0, 3001, 10):
                bonus = dec(bonus_for_distance(dec(meters), dec(cfg["max"]), CFG)["bonus"])
                if previous is not None:
                    self.assertLessEqual(bonus, previous, f"{name}: бонус вырос на {meters} м")
                previous = bonus

    def test_zero_distance_gives_full_bonus(self):
        for name, cfg in CFG["bonuses"]["categories"].items():
            bonus = dec(bonus_for_distance(Decimal(0), dec(cfg["max"]), CFG)["bonus"])
            self.assertEqual(bonus, dec(cfg["max"]), name)

    def test_bonus_is_zero_beyond_last_threshold(self):
        last = max(t["up_to"] for t in CFG["bonuses"]["time_thresholds_min"])
        for name, cfg in CFG["bonuses"]["categories"].items():
            bonus = dec(
                bonus_for_distance(distance_for_minutes(last + 1), dec(cfg["max"]), CFG)["bonus"]
            )
            self.assertEqual(bonus, 0, name)

    def test_bonus_never_exceeds_category_max(self):
        for name, cfg in CFG["bonuses"]["categories"].items():
            for meters in range(0, 3001, 50):
                bonus = dec(bonus_for_distance(dec(meters), dec(cfg["max"]), CFG)["bonus"])
                self.assertLessEqual(bonus, dec(cfg["max"]), f"{name} на {meters} м")


class TestPenaltyMonotonicity(unittest.TestCase):
    def test_penalty_magnitude_never_grows_with_distance(self):
        for name, cfg in CFG["penalties"].items():
            if not isinstance(cfg, dict) or not cfg.get("enabled", False):
                continue
            weight = max_weight(cfg)
            radius = int(dec(cfg["radius_m"]))
            previous = None
            for meters in range(0, radius + 51):
                penalty = penalty_for_distance(
                    dec(meters), dec(cfg["max"]), weight, dec(cfg["radius_m"])
                )
                magnitude = abs(penalty)
                if previous is not None:
                    self.assertLessEqual(
                        magnitude, previous, f"{name}: |штраф| вырос на {meters} м"
                    )
                previous = magnitude

    def test_penalty_zero_at_radius_and_positive_just_inside(self):
        for name, cfg in CFG["penalties"].items():
            if not isinstance(cfg, dict) or not cfg.get("enabled", False):
                continue
            weight = max_weight(cfg)
            radius = dec(cfg["radius_m"])
            at_radius = penalty_for_distance(radius, dec(cfg["max"]), weight, radius)
            self.assertEqual(at_radius, 0, name)
            just_inside = penalty_for_distance(radius - 1, dec(cfg["max"]), weight, radius)
            self.assertLess(just_inside, 0, name)
            outside = penalty_for_distance(radius + 1, dec(cfg["max"]), weight, radius)
            self.assertEqual(outside, 0, name)

    def test_road_class_weights_are_ordered(self):
        road = CFG["penalties"]["road"]
        radius = dec(road["radius_m"])
        distance = Decimal(60)
        penalties = {
            name: penalty_for_distance(distance, dec(road["max"]), dec(weight), radius)
            for name, weight in road["classes"].items()
        }
        self.assertLessEqual(penalties["trunk"], penalties["primary"])
        self.assertLessEqual(penalties["primary"], penalties["secondary"])


class TestIndexBounds(unittest.TestCase):
    def test_clamp_at_both_ends(self):
        low = dec(CFG["index"]["min"])
        high = dec(CFG["index"]["max"])
        self.assertEqual(finalize_index(low - 3, CFG), low)
        self.assertEqual(finalize_index(high + 3, CFG), high)

    def test_extreme_combinations_stay_in_range(self):
        base = dec(CFG["index"]["base"])
        low = dec(CFG["index"]["min"])
        high = dec(CFG["index"]["max"])
        bonus_maxima = [dec(cfg["max"]) for cfg in CFG["bonuses"]["categories"].values()]
        penalty_maxima = [
            dec(cfg["max"])
            for cfg in CFG["penalties"].values()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]
        # 2^(бонусы+штрафы) комбинаций «0 или максимум»: 512 вариантов.
        for bonus_mask in range(1 << len(bonus_maxima)):
            total = base
            for index, value in enumerate(bonus_maxima):
                if bonus_mask & (1 << index):
                    total += value
            for penalty_mask in range(1 << len(penalty_maxima)):
                candidate = total
                for index, value in enumerate(penalty_maxima):
                    if penalty_mask & (1 << index):
                        candidate += value
                result = finalize_index(candidate, CFG)
                self.assertGreaterEqual(result, low)
                self.assertLessEqual(result, high)

    def test_all_penalties_at_zero_distance_clamp_to_min(self):
        base = dec(CFG["index"]["base"])
        total = base
        for cfg in CFG["penalties"].values():
            if not isinstance(cfg, dict) or not cfg.get("enabled", False):
                continue
            total += penalty_for_distance(
                Decimal(0), dec(cfg["max"]), max_weight(cfg), dec(cfg["radius_m"])
            )
        self.assertEqual(finalize_index(total, CFG), dec(CFG["index"]["min"]))


class TestDeterminism(unittest.TestCase):
    def test_repeated_calls_are_equal(self):
        for meters in (0, 123, 999, 2500):
            first = bonus_for_distance(dec(meters), Decimal("1.0"), CFG)
            second = bonus_for_distance(dec(meters), Decimal("1.0"), CFG)
            self.assertEqual(first, second)
            self.assertTrue(math.isfinite(first["minutes"]))
            self.assertTrue(math.isfinite(first["bonus"]))

    def test_walking_minutes_is_strictly_positive(self):
        self.assertGreater(walking_minutes(Decimal(1), SPEED), 0)
        self.assertEqual(walking_minutes(Decimal(0), SPEED), 0)


if __name__ == "__main__":
    unittest.main()
