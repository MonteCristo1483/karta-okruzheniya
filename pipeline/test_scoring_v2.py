"""Юнит-тесты скоринга v2 (METHODOLOGY-V2). Числа — из config/scoring.json."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring_v2 import (  # noqa: E402
    access_quality,
    best_transport_quality,
    build_v2_block,
    dec,
    density_from_floor_area,
    density_pressure,
    green_quality,
    home_quality,
    transport_quality_cfg,
    v2_index,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class TestWeights(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(CFG["v2"]["weights"].values()), 100)

    def test_all_factors_present(self):
        self.assertEqual(
            set(CFG["v2"]["weights"]),
            {"transport", "access", "green", "home", "density"},
        )

    def test_access_type_weights_sum_to_one(self):
        weights = [
            spec["weight"] for spec in CFG["v2"]["access"]["types"].values()
        ]
        self.assertAlmostEqual(sum(weights), 1.0, places=6)


class TestTransport(unittest.TestCase):
    def test_trunk_at_zero_is_full(self):
        entry = transport_quality_cfg("trunk", 0, CFG)
        self.assertEqual(entry["value"], 1.0)
        self.assertEqual(entry["status"], "ok")

    def test_trunk_half_radius_uses_power_1_5(self):
        entry = transport_quality_cfg("trunk", 125, CFG)
        self.assertAlmostEqual(entry["value"], 0.5 ** 1.5, places=3)

    def test_at_radius_is_zero(self):
        for cls, spec in CFG["v2"]["transport"]["classes"].items():
            entry = transport_quality_cfg(cls, spec["radius_m"], CFG)
            self.assertEqual(entry["value"], 0, cls)
            self.assertEqual(entry["status"], "outside", cls)

    def test_beyond_radius_is_zero(self):
        entry = transport_quality_cfg("tertiary", 61, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "outside")

    def test_unknown_road_class_is_not_classified(self):
        entry = transport_quality_cfg("residential", 10, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")

    def test_best_class_wins(self):
        entry = best_transport_quality(
            {"trunk": 200.0, "tertiary": 10.0}, CFG
        )
        self.assertEqual(entry["class"], "tertiary")
        self.assertGreater(entry["value"], 0)


class TestGreen(unittest.TestCase):
    def test_no_park_is_known_zero(self):
        entry = green_quality([], CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")

    def test_unknown_area_is_not_classified(self):
        entry = green_quality([{"area_m2": None, "distance_m": 100.0}], CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")

    def test_half_hectare_fully_inside_gives_full_bonus(self):
        entry = green_quality([{"area_m2": 5000.0, "distance_m": 0.0}], CFG)
        self.assertEqual(entry["value"], 1.0)
        self.assertEqual(entry["status"], "ok")

    def test_park_extends_outside_radius_is_zero(self):
        entry = green_quality([{"area_m2": 5000.0, "distance_m": 280.0}], CFG)
        self.assertEqual(entry["value"], 0)

    def test_park_smaller_than_min_area_is_zero(self):
        entry = green_quality([{"area_m2": 4000.0, "distance_m": 10.0}], CFG)
        self.assertEqual(entry["value"], 0)

    def test_polygon_fully_inside_gives_bonus(self):
        entry = green_quality(
            [{"area_m2": 8000.0, "distance_m": 120.0, "max_distance_m": 299.0}],
            CFG,
        )
        self.assertEqual(entry["value"], 1.0)
        self.assertEqual(entry["status"], "ok")

    def test_polygon_far_vertex_outside_radius_is_zero(self):
        entry = green_quality(
            [{"area_m2": 8000.0, "distance_m": 120.0, "max_distance_m": 310.0}],
            CFG,
        )
        self.assertEqual(entry["value"], 0)


class TestAccess(unittest.TestCase):
    def distances(self, share: float) -> dict[str, float]:
        return {
            name: spec["radius_m"] * share
            for name, spec in CFG["v2"]["access"]["types"].items()
        }

    def test_all_close_is_full(self):
        entry = access_quality(self.distances(0.0), CFG)
        self.assertEqual(entry["value"], 1.0)
        self.assertEqual(entry["status"], "ok")

    def test_at_radius_is_zero_but_known(self):
        entry = access_quality(self.distances(1.0), CFG)
        self.assertEqual(entry["value"], 0.0)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["missing"], [])

    def test_half_radius_is_half(self):
        entry = access_quality(self.distances(0.5), CFG)
        self.assertAlmostEqual(entry["value"], 0.5, places=3)

    def test_missing_layer_renormalizes_and_flags(self):
        distances = self.distances(0.0)
        distances["polyclinic"] = None
        entry = access_quality(distances, CFG)
        self.assertEqual(entry["value"], 1.0)
        self.assertIn("polyclinic", entry["missing"])
        self.assertEqual(entry["status"], "not_classified")


class TestHome(unittest.TestCase):
    def test_panel_before_1970(self):
        entry = home_quality(1965, None, "Стены из несущих панелей", CFG)
        self.assertEqual(entry["rule"], "panel_pre_1970")
        self.assertEqual(entry["value"], 0.4)

    def test_panel_1970_1990(self):
        entry = home_quality(1985, "П-44", None, CFG)
        self.assertEqual(entry["rule"], "panel_1970_1990")
        self.assertEqual(entry["value"], 0.6)

    def test_brick_1990_plus(self):
        entry = home_quality(2000, None, "Стены кирпичные", CFG)
        self.assertEqual(entry["rule"], "brick_monolith_1990")
        self.assertEqual(entry["value"], 0.8)

    def test_new_build_2015_plus(self):
        entry = home_quality(2018, None, None, CFG)
        self.assertEqual(entry["rule"], "new_build_2015")
        self.assertEqual(entry["value"], 1.0)

    def test_no_year_is_zero_and_flagged(self):
        entry = home_quality(None, None, None, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")

    def test_known_year_unknown_material_is_fallback(self):
        entry = home_quality(1995, None, None, CFG)
        self.assertEqual(entry["rule"], "fallback_known")
        self.assertEqual(entry["value"], 0.5)

    def test_series_detects_panel(self):
        entry = home_quality(1980, "1-515", None, CFG)
        self.assertEqual(entry["rule"], "panel_1970_1990")


class TestDensity(unittest.TestCase):
    def test_below_comfort_is_no_pressure(self):
        self.assertEqual(density_pressure(5000, CFG)["value"], 0.0)

    def test_above_critical_is_full_pressure(self):
        self.assertEqual(density_pressure(50000, CFG)["value"], 1.0)

    def test_middle_is_linear(self):
        comfort = CFG["v2"]["density"]["comfort_m2_per_ha"]
        critical = CFG["v2"]["density"]["critical_m2_per_ha"]
        middle = (comfort + critical) / 2
        self.assertAlmostEqual(density_pressure(middle, CFG)["value"], 0.5, places=3)

    def test_unknown_density_is_not_classified(self):
        self.assertEqual(density_pressure(None, CFG)["status"], "not_classified")

    def test_floor_area_to_density(self):
        radius = CFG["v2"]["density"]["radius_m"]
        circle_ha = 3.141592653589793 * radius ** 2 / 10000
        self.assertAlmostEqual(
            density_from_floor_area(circle_ha * 10000, radius), 10000, places=3
        )


class TestIndex(unittest.TestCase):
    def test_all_unknown_stays_neutral(self):
        values = {name: 0.0 for name in CFG["v2"]["weights"]}
        self.assertEqual(v2_index(values, CFG), CFG["index"]["base"])

    def test_best_quality(self):
        values = {"transport": 0.0, "access": 0.0, "green": 1.0,
                  "home": 1.0, "density": 0.0}
        self.assertEqual(v2_index(values, CFG), dec("8.1"))

    def test_max_transport_penalty(self):
        values = {"transport": 1.0, "access": 0, "green": 0,
                  "home": 0, "density": 0}
        self.assertEqual(v2_index(values, CFG), dec("1.2"))

    def test_max_access_bonus(self):
        values = {"transport": 0, "access": 1.0, "green": 0,
                  "home": 0, "density": 0}
        self.assertEqual(v2_index(values, CFG), dec("7.5"))

    def test_max_density_penalty(self):
        values = {"transport": 0, "access": 0, "green": 0,
                  "home": 0, "density": 1.0}
        self.assertEqual(v2_index(values, CFG), dec("4.4"))

    def test_unknown_factor_adds_flag(self):
        block = build_v2_block(
            {"value": 0.5, "status": "ok", "class": "primary", "distance_m": 20},
            {"value": 0.0, "status": "ok", "types": {}, "missing": []},
            {"value": 0.0, "status": "not_classified", "distance_m": 120},
            {"value": 0.6, "status": "ok", "rule": "panel_1970_1990", "year": 1985},
            {"value": 1.0, "status": "ok", "density_m2_per_ha": 5000},
            CFG,
        )
        self.assertIn("green:not_classified", block["flags"])
        self.assertEqual(block["factors"]["transport"]["contribution"], -1.9)
        self.assertEqual(block["index"], 3.2)

    def test_missing_access_layer_adds_flags(self):
        block = build_v2_block(
            {"value": 0.0, "status": "outside", "class": None},
            {"value": 0.7, "status": "not_classified", "types": {},
             "missing": ["polyclinic"]},
            {"value": 1.0, "status": "ok"},
            {"value": 0.6, "status": "ok"},
            {"value": 0.0, "status": "ok"},
            CFG,
        )
        self.assertIn("access:not_classified", block["flags"])
        self.assertIn("access:polyclinic:not_classified", block["flags"])


if __name__ == "__main__":
    unittest.main()
