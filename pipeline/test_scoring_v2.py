"""Юнит-тесты скоринга v2 (METHODOLOGY-V2). Числа — из config/scoring.json."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring_v2 import (  # noqa: E402
    SECTOR_LABELS,
    access_quality,
    bearing_between,
    best_transport_quality,
    build_v2_block,
    dec,
    density_from_floor_area,
    density_pressure,
    green_quality,
    home_quality,
    industry_pressure,
    transport_quality_cfg,
    v2_index,
    wind_pressure,
    wind_sector,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
ROSE_PATH = Path(__file__).resolve().parent.parent / "config" / "wind_rose.json"
ROSE = json.loads(ROSE_PATH.read_text(encoding="utf-8"))
UNIFORM_ROSE = {"shares": {label: 1.0 / 16 for label in SECTOR_LABELS}}


class TestWeights(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(CFG["v2"]["weights"].values()), 100)

    def test_all_factors_present(self):
        self.assertEqual(
            set(CFG["v2"]["weights"]),
            {"transport", "industry", "access", "green", "home", "density", "wind"},
        )

    def test_wind_weight_is_10_and_others_scaled(self):
        weights = CFG["v2"]["weights"]
        self.assertEqual(weights["wind"], 10)
        for name, old in {"transport": 30, "industry": 20, "access": 20,
                          "green": 15, "home": 10, "density": 5}.items():
            self.assertAlmostEqual(weights[name], old * 0.9, places=6, msg=name)

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


class TestIndustry(unittest.TestCase):
    def test_sanpin_radii(self):
        classes = CFG["v2"]["industry"]["classes"]
        self.assertEqual(classes["I"]["radius_m"], 1000)
        self.assertEqual(classes["II"]["radius_m"], 500)
        self.assertEqual(classes["III"]["radius_m"], 300)
        self.assertEqual(classes["IV"]["radius_m"], 100)
        self.assertEqual(classes["V"]["radius_m"], 50)

    def test_class_i_at_zero_is_full(self):
        entry = industry_pressure(
            [{"id": "x", "name": "МНПЗ", "hazard_class": "I", "distance_m": 0,
              "status": "operating"}], CFG
        )
        self.assertEqual(entry["value"], 1.0)
        self.assertEqual(entry["status"], "ok")

    def test_half_radius_uses_power_1_5(self):
        entry = industry_pressure(
            [{"id": "x", "name": "ТЭЦ", "hazard_class": "III", "distance_m": 150,
              "status": "operating"}], CFG
        )
        weight = CFG["v2"]["industry"]["classes"]["III"]["weight"]
        self.assertAlmostEqual(entry["value"], weight * 0.5 ** 1.5, places=3)

    def test_service_near_190m_does_not_penalize(self):
        entry = industry_pressure(
            [{"id": "x", "name": "автосервис", "hazard_class": "V",
              "distance_m": 190, "status": "operating"}],
            CFG,
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "outside")

    def test_unknown_class_is_not_classified(self):
        entry = industry_pressure(
            [{"id": "x", "name": "склад", "hazard_class": "VI",
              "distance_m": 20, "status": "operating"}], CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")

    def test_no_sources_is_known_zero(self):
        entry = industry_pressure([], CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")

    def test_inside_unclassified_polygon_is_zero_with_flag(self):
        entry = industry_pressure([], CFG, inside_unclassified_polygon=True)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")
        block = build_v2_block(
            {"value": 0.0, "status": "outside", "class": None},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            CFG,
            industry=entry,
        )
        self.assertIn("industry:not_classified", block["flags"])
        self.assertEqual(block["index"], CFG["index"]["base"])

    def test_inside_polygon_with_far_source_is_still_not_classified(self):
        entry = industry_pressure(
            [{"id": "x", "name": "ТЭЦ", "hazard_class": "III",
              "distance_m": 900, "status": "operating"}],
            CFG,
            inside_unclassified_polygon=True,
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")

    def test_strictest_source_wins(self):
        entry = industry_pressure(
            [
                {"id": "a", "name": "котельная", "hazard_class": "IV",
                 "distance_m": 20, "status": "operating"},
                {"id": "b", "name": "ТЭЦ", "hazard_class": "III",
                 "distance_m": 100, "status": "operating"},
            ],
            CFG,
        )
        self.assertEqual(entry["hazard_class"], "III")
        self.assertGreater(entry["value"], 0)

    def test_conservation_does_not_penalize(self):
        entry = industry_pressure(
            [{"id": "msz-2", "name": "МСЗ-2", "hazard_class": "I",
              "distance_m": 0, "status": "conservation"}], CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")
        self.assertNotIn("unknown_status", entry)

    def test_outside_moscow_does_not_penalize(self):
        entry = industry_pressure(
            [{"id": "moskoks", "name": "Москокс", "hazard_class": "I",
              "distance_m": 0, "status": "outside_moscow"}], CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")
        block = build_v2_block(
            {"value": 0.0, "status": "outside", "class": None},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            CFG,
            industry=entry,
        )
        self.assertNotIn("industry:unknown_status", block["flags"])
        self.assertNotIn("industry:not_classified", block["flags"])
        self.assertEqual(block["index"], CFG["index"]["base"])

    def test_unknown_status_zero_with_flag(self):
        entry = industry_pressure(
            [{"id": "polymetall", "name": "МЗП", "hazard_class": "III",
              "distance_m": 0, "status": "unknown"}], CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "unknown_status")
        block = build_v2_block(
            {"value": 0.0, "status": "outside", "class": None},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "known_zero"},
            CFG,
            industry=entry,
        )
        self.assertIn("industry:unknown_status", block["flags"])
        self.assertNotIn("industry:not_classified", block["flags"])
        self.assertEqual(block["index"], CFG["index"]["base"])

    def test_missing_status_is_not_penalized(self):
        entry = industry_pressure(
            [{"id": "x", "name": "неизвестный", "hazard_class": "I",
              "distance_m": 0}], CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "unknown_status")

    def test_operating_source_wins_over_unknown_status_nearby(self):
        entry = industry_pressure(
            [
                {"id": "polymetall", "name": "МЗП", "hazard_class": "III",
                 "distance_m": 10, "status": "unknown"},
                {"id": "tec", "name": "ТЭЦ", "hazard_class": "III",
                 "distance_m": 100, "status": "operating"},
            ],
            CFG,
        )
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["name"], "ТЭЦ")
        self.assertTrue(entry["unknown_status"])

    def test_max_industry_penalty(self):
        values = {"transport": 0, "industry": 1.0, "access": 0, "green": 0,
                  "home": 0, "density": 0}
        self.assertEqual(v2_index(values, CFG), dec("3.2"))


SOURCES_PATH = Path(__file__).resolve().parent.parent / "config" / "industry_sources.json"
SOURCES = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))["sources"]


class TestIndustrySources(unittest.TestCase):
    def test_manual_list_size(self):
        self.assertGreaterEqual(len(SOURCES), 20)
        self.assertLessEqual(len(SOURCES), 40)

    def test_classes_are_valid(self):
        classes = set(CFG["v2"]["industry"]["classes"])
        for source in SOURCES:
            self.assertIn(source["hazard_class"], classes, source["id"])

    def test_ids_are_unique(self):
        ids = [source["id"] for source in SOURCES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_coordinates_inside_moscow_region(self):
        for source in SOURCES:
            self.assertTrue(55.5 <= source["lat"] <= 56.0, source["id"])
            self.assertTrue(37.3 <= source["lon"] <= 38.0, source["id"])

    def test_coordinates_are_verifiable_osm(self):
        for source in SOURCES:
            self.assertRegex(source["source"], r"OSM (node|way|relation) \d+")

    def test_status_and_provenance_fields(self):
        industry = CFG["v2"]["industry"]
        allowed = (
            set(industry["penalizing_statuses"])
            | set(industry["unknown_statuses"])
            | {"conservation", "outside_moscow"}
        )
        for source in SOURCES:
            self.assertIn(source["status"], allowed, source["id"])
            self.assertEqual(source["verified_at"], "2026-11-26", source["id"])
            self.assertTrue(
                source["source_url"].startswith("https://www.openstreetmap.org/"),
                source["id"],
            )
            self.assertIn(source["osm_type"], {"node", "way", "relation"}, source["id"])
            self.assertIsInstance(source["osm_id"], int, source["id"])

    def test_verified_statuses_by_object(self):
        by_id = {source["id"]: source for source in SOURCES}
        self.assertEqual(by_id["msz-2"]["status"], "conservation")
        self.assertEqual(by_id["msz-4"]["status"], "operating")
        self.assertEqual(by_id["moskoks"]["status"], "outside_moscow")
        self.assertEqual(by_id["polymetall"]["status"], "unknown")
        self.assertEqual(by_id["polymetall"]["hazard_class"], "III")
        self.assertEqual(by_id["mnpmz"]["status"], "operating")
        self.assertEqual(by_id["kuryanovskaya"]["status"], "operating")

    def test_only_operating_penalizes(self):
        industry = CFG["v2"]["industry"]
        self.assertEqual(industry["penalizing_statuses"], ["operating"])
        self.assertEqual(industry["unknown_statuses"], ["unknown"])


class TestWind(unittest.TestCase):
    def test_rose_has_16_sectors_and_sums_to_one(self):
        self.assertEqual(len(ROSE["shares"]), 16)
        self.assertEqual(set(ROSE["shares"]), set(SECTOR_LABELS))
        self.assertAlmostEqual(sum(ROSE["shares"].values()), 1.0, places=5)

    def test_top_three_sectors_are_westerly(self):
        top = sorted(ROSE["shares"].items(), key=lambda item: item[1], reverse=True)[:3]
        self.assertEqual({name for name, _ in top}, {"W", "WSW", "SW"})

    def test_sector_boundaries(self):
        self.assertEqual(wind_sector(0), "N")
        self.assertEqual(wind_sector(359.9), "N")
        self.assertEqual(wind_sector(348.75), "N")
        self.assertEqual(wind_sector(348.74), "NNW")
        self.assertEqual(wind_sector(11.24), "N")
        self.assertEqual(wind_sector(11.25), "NNE")
        self.assertEqual(wind_sector(22.5), "NNE")
        self.assertEqual(wind_sector(33.75), "NE")
        self.assertEqual(wind_sector(45), "NE")
        self.assertEqual(wind_sector(90), "E")
        self.assertEqual(wind_sector(180), "S")
        self.assertEqual(wind_sector(270), "W")

    def test_bearing_between_cardinal_directions(self):
        self.assertAlmostEqual(bearing_between(0, 0, 1, 0), 0.0, places=3)
        self.assertAlmostEqual(bearing_between(0, 0, 0, 1), 90.0, places=3)
        self.assertAlmostEqual(bearing_between(1, 0, 0, 0), 180.0, places=3)
        self.assertAlmostEqual(bearing_between(0, 1, 0, 0), 270.0, places=3)

    def source(self, bearing_deg, distance_m=0.0, hazard_class="I",
               status="operating"):
        return {
            "id": "x", "name": "тест", "hazard_class": hazard_class,
            "status": status, "distance_m": distance_m,
            "bearing_deg": bearing_deg,
        }

    def test_source_to_north_uses_southerly_wind(self):
        # Дом к северу от источника: выброс несёт южный ветер (180°).
        entry = wind_pressure([self.source(0)], UNIFORM_ROSE, CFG)
        self.assertEqual(entry["sector"], "S")
        self.assertEqual(entry["value"], 1.0)

    def test_half_radius_uses_power_1_5(self):
        entry = wind_pressure([self.source(0, 500)], UNIFORM_ROSE, CFG)
        self.assertAlmostEqual(entry["value"], 0.5 ** 1.5, places=4)

    def test_wind_value_matches_rose_share(self):
        entry = wind_pressure([self.source(180)], ROSE, CFG)
        # bearing 180 -> ветер с севера; нормировка на максимум румба.
        expected = ROSE["shares"]["N"] / max(ROSE["shares"].values())
        self.assertAlmostEqual(entry["value"], expected, places=4)

    def test_no_sources_is_exactly_zero(self):
        entry = wind_pressure([], ROSE, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")
        self.assertEqual(entry["sources"], 0)

    def test_non_operating_source_does_not_penalize(self):
        entry = wind_pressure(
            [self.source(0, status="conservation")], ROSE, CFG
        )
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")

    def test_source_outside_radius_is_zero(self):
        entry = wind_pressure([self.source(0, 1000)], ROSE, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "known_zero")

    def test_zero_rose_with_source_is_not_classified(self):
        zero = {"shares": {label: 0.0 for label in SECTOR_LABELS}}
        entry = wind_pressure([self.source(0)], zero, CFG)
        self.assertEqual(entry["value"], 0)
        self.assertEqual(entry["status"], "not_classified")
        self.assertEqual(entry["reason"], "empty_wind_rose")

    def test_multiple_sources_sum_and_clamp(self):
        sources = [self.source(0), self.source(90), self.source(180)]
        entry = wind_pressure(sources, UNIFORM_ROSE, CFG)
        self.assertEqual(entry["sources"], 3)
        self.assertEqual(entry["value"], 1.0)

    def test_max_wind_penalty(self):
        values = {"transport": 0, "industry": 0, "access": 0, "green": 0,
                  "home": 0, "density": 0, "wind": 1.0}
        self.assertEqual(v2_index(values, CFG), dec("4.0"))

    def test_no_sources_keeps_neutral_index(self):
        block = build_v2_block(
            {"value": 0.0, "status": "outside", "class": None},
            {"value": 0.0, "status": "known_zero", "types": {}, "missing": []},
            {"value": 0.0, "status": "known_zero"},
            {"value": 0.0, "status": "ok"},
            {"value": 0.0, "status": "ok"},
            CFG,
            wind=wind_pressure([], ROSE, CFG),
        )
        self.assertEqual(block["factors"]["wind"]["value"], 0.0)
        self.assertEqual(block["index"], CFG["index"]["base"])
        self.assertNotIn("wind:not_classified", block["flags"])


class TestIndex(unittest.TestCase):
    def test_all_unknown_stays_neutral(self):
        values = {name: 0.0 for name in CFG["v2"]["weights"]}
        self.assertEqual(v2_index(values, CFG), CFG["index"]["base"])

    def test_best_quality(self):
        values = {"transport": 0.0, "industry": 0.0, "access": 0.0,
                  "green": 1.0, "home": 1.0, "density": 0.0}
        self.assertEqual(v2_index(values, CFG), dec("7.3"))

    def test_max_transport_penalty(self):
        values = {"transport": 1.0, "industry": 0, "access": 0, "green": 0,
                  "home": 0, "density": 0}
        self.assertEqual(v2_index(values, CFG), dec("2.3"))

    def test_max_access_bonus(self):
        values = {"transport": 0, "industry": 0, "access": 1.0, "green": 0,
                  "home": 0, "density": 0}
        self.assertEqual(v2_index(values, CFG), dec("6.8"))

    def test_max_density_penalty(self):
        values = {"transport": 0, "industry": 0, "access": 0, "green": 0,
                  "home": 0, "density": 1.0}
        self.assertEqual(v2_index(values, CFG), dec("4.6"))

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
        self.assertEqual(block["factors"]["transport"]["contribution"], -1.35)
        self.assertEqual(block["index"], 3.7)

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
