"""Юнит-тесты валидатора выгрузки (HANDOFF §8).

Запуск из корня репозитория:
  pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_export import CONFIG_PATH, load_config, validate_export  # noqa: E402

GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[37.5600, 55.7600], [37.5610, 55.7600],
                      [37.5610, 55.7610], [37.5600, 55.7610],
                      [37.5600, 55.7600]]]],
}

CFG = load_config(CONFIG_PATH)


def zero_bonus() -> dict:
    return {
        name: {"minutes": 30.0, "distance_m": 2000.0, "multiplier": 0.0, "bonus": 0.0}
        for name in CFG["bonuses"]["categories"]
    }


def zero_penalty() -> dict:
    entries = {
        "road": {"class": "trunk", "weight": 1.0, "penalty": 0.0, "distance_m": 500.0},
        "railway": {"class": "rail", "weight": 1.0, "penalty": 0.0, "distance_m": 500.0},
        "construction": {"class": "construction", "weight": 1.0, "penalty": 0.0,
                         "distance_m": 500.0},
        "industrial": {"class": "industrial", "weight": 1.0, "penalty": 0.0,
                       "distance_m": 500.0},
    }
    return {name: entries[name] for name in CFG["penalties"] if isinstance(CFG["penalties"][name], dict)}


def building(building_id: int = 1, index: float = 5.0, **overrides) -> dict:
    props = {
        "id": building_id,
        "index": index,
        "height": 20.0,
        "address": "ул. Тестовая, 1",
        "year": 2000,
        "breakdown": {
            "base": 5.0,
            "index": index,
            "bonuses": zero_bonus(),
            "penalties": zero_penalty(),
        },
    }
    props.update(overrides)
    return {"type": "Feature", "properties": props, "geometry": copy.deepcopy(GEOMETRY)}


def poi(poi_type: str = "park", name: str = "Парк", subtype: str | None = None) -> dict:
    return {
        "type": "Feature",
        "properties": {"type": poi_type, "subtype": subtype, "name": name},
        "geometry": {"type": "Point", "coordinates": [37.56, 55.76]},
    }


def default_poi() -> list[dict]:
    return [
        poi("park", "Парк"),
        poi("school", "Школа"),
        poi("kindergarten", "Сад"),
        poi("subway_entrance", "Метро"),
        poi("rail_platform", "МЦД"),
        poi("shop", "Магазин", "supermarket"),
    ]


def redzone() -> dict:
    return {
        "type": "Feature",
        "properties": {"kind": "road", "class": "trunk"},
        "geometry": {"type": "LineString", "coordinates": [[37.55, 55.75], [37.57, 55.77]]},
    }


def write_export(root: Path, buildings: list[dict] | None = None,
                 pois: list[dict] | None = None,
                 redzones: list[dict] | None = None) -> Path:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    files = {
        "presnya_buildings.geojson": buildings if buildings is not None else [building()],
        "presnya_poi.geojson": pois if pois is not None else default_poi(),
        "presnya_redzones.geojson": redzones if redzones is not None else [redzone()],
    }
    for name, features in files.items():
        payload = {"type": "FeatureCollection", "features": features}
        (data / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return data


class ValidatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_validator(self, **kwargs):
        data = write_export(self.root, **kwargs)
        return validate_export(data, CONFIG_PATH)

    def codes(self, report, severity: str) -> set[str]:
        return {i.code for i in report.issues if i.severity == severity}


class TestValidExport(ValidatorTestCase):
    def test_valid_export_has_no_critical(self):
        report = self.run_validator()
        self.assertEqual(report.critical(), [])
        self.assertEqual(report.stats["buildings"], 1)
        self.assertEqual(report.coverage["address_pct"], 100.0)

    def test_clamped_low_is_ok(self):
        # Все штрафы в упор: 5 - 2.5 - 1.5 - 1.0 - 1.5 = -1.5 → кламп в 0.
        bn = building(index=0.0)
        penalties = bn["properties"]["breakdown"]["penalties"]
        penalties["road"].update(distance_m=0.0, penalty=-2.5)
        penalties["railway"].update(distance_m=0.0, penalty=-1.5)
        penalties["construction"].update(distance_m=0.0, penalty=-1.0)
        penalties["industrial"].update(distance_m=0.0, penalty=-1.5)
        report = self.run_validator(buildings=[bn])
        self.assertEqual(report.critical(), [])

    def test_clamped_high_is_ok(self):
        # Нереалистичная база 8 плюс максимумы бонусов: 8 + 4.5 = 12.5 → кламп в 10.
        bn = building(index=10.0)
        breakdown = bn["properties"]["breakdown"]
        breakdown["base"] = 8.0
        maxima = {name: cfg["max"] for name, cfg in CFG["bonuses"]["categories"].items()}
        for name, maximum in maxima.items():
            breakdown["bonuses"][name].update(
                minutes=1.0, distance_m=50.0, multiplier=1.0, bonus=maximum
            )
        report = self.run_validator(buildings=[bn])
        self.assertEqual(report.critical(), [])
        self.assertIn("base_mismatch", self.codes(report, "warning"))


class TestCriticalErrors(ValidatorTestCase):
    def test_duplicate_id(self):
        report = self.run_validator(buildings=[building(1), building(1)])
        self.assertIn("duplicate_id", self.codes(report, "critical"))

    def test_index_out_of_range(self):
        report = self.run_validator(buildings=[building(index=11.0)])
        self.assertIn("index_out_of_range", self.codes(report, "critical"))

    def test_breakdown_does_not_sum_to_index(self):
        bn = building(index=5.0)
        bn["properties"]["breakdown"]["bonuses"]["parks"]["bonus"] = 1.0
        report = self.run_validator(buildings=[bn])
        self.assertIn("sum_mismatch", self.codes(report, "critical"))

    def test_negative_distance(self):
        bn = building()
        bn["properties"]["breakdown"]["bonuses"]["schools"]["distance_m"] = -5.0
        report = self.run_validator(buildings=[bn])
        self.assertIn("negative_value", self.codes(report, "critical"))

    def test_positive_penalty(self):
        bn = building()
        bn["properties"]["breakdown"]["penalties"]["road"]["penalty"] = 0.5
        report = self.run_validator(buildings=[bn])
        self.assertIn("positive_penalty", self.codes(report, "critical"))

    def test_penalty_bigger_than_config_max(self):
        bn = building()
        bn["properties"]["breakdown"]["penalties"]["road"]["penalty"] = -3.0
        report = self.run_validator(buildings=[bn])
        self.assertIn("penalty_too_big", self.codes(report, "critical"))

    def test_broken_json(self):
        data = write_export(self.root)
        (data / "presnya_buildings.geojson").write_text("{", encoding="utf-8")
        report = validate_export(data, CONFIG_PATH)
        self.assertIn("broken_json", self.codes(report, "critical"))

    def test_geometry_outside_moscow(self):
        bn = building()
        bn["geometry"]["coordinates"] = [[[[30.0, 50.0], [30.1, 50.0], [30.1, 50.1],
                                           [30.0, 50.0]]]]
        report = self.run_validator(buildings=[bn])
        self.assertIn("outside_moscow", self.codes(report, "critical"))

    def test_negative_route(self):
        data = write_export(self.root)
        routes = data / "routes"
        routes.mkdir()
        (routes / "1.json").write_text(json.dumps({
            "categories": {"transport": {"minutes": -1, "distance_m": 10,
                                         "geometry": {"type": "LineString",
                                                      "coordinates": []}}}
        }), encoding="utf-8")
        report = validate_export(data, CONFIG_PATH)
        self.assertIn("negative_route", self.codes(report, "critical"))


class TestWarnings(ValidatorTestCase):
    def test_number_only_address_is_warning(self):
        report = self.run_validator(buildings=[building(address="16")])
        self.assertIn("address_without_street", self.codes(report, "warning"))
        self.assertEqual(report.critical(), [])

    def test_suspicious_year_is_warning(self):
        report = self.run_validator(buildings=[building(year=1700)])
        self.assertIn("suspicious_year", self.codes(report, "warning"))
        self.assertEqual(report.critical(), [])

    def test_duplicate_poi_is_warning(self):
        report = self.run_validator(pois=default_poi() + [poi("park", "Парк")])
        self.assertIn("duplicate_poi", self.codes(report, "warning"))

    def test_missing_rail_platform_is_warning(self):
        pois = [p for p in default_poi() if p["properties"]["type"] != "rail_platform"]
        report = self.run_validator(pois=pois)
        messages = [i.message for i in report.issues if i.code == "missing_poi_type"]
        self.assertTrue(any("rail_platform" in m for m in messages))

    def test_unknown_redzone_is_warning(self):
        zone = redzone()
        zone["properties"]["kind"] = "noise"
        report = self.run_validator(redzones=[zone])
        self.assertIn("unknown_redzone", self.codes(report, "warning"))


class TestIsochrones(ValidatorTestCase):
    def iso_geom(self) -> dict:
        return {
            "type": "Polygon",
            "coordinates": [[[37.56, 55.76], [37.561, 55.76],
                             [37.561, 55.761], [37.56, 55.76]]],
        }

    def write_iso(self, payload: dict) -> Path:
        data = write_export(self.root)
        iso = data / "isochrones"
        iso.mkdir()
        (iso / "1.json").write_text(json.dumps(payload), encoding="utf-8")
        return data

    def test_valid_isochrones(self):
        data = self.write_iso({"5": self.iso_geom(), "10": self.iso_geom(),
                               "15": self.iso_geom()})
        report = validate_export(data, CONFIG_PATH)
        self.assertEqual(report.critical(), [])
        self.assertEqual(report.stats["isochrone_files"], 1)

    def test_missing_contour_is_warning(self):
        data = self.write_iso({"5": self.iso_geom()})
        report = validate_export(data, CONFIG_PATH)
        self.assertIn("bad_contours", self.codes(report, "warning"))

    def test_outside_moscow_is_critical(self):
        bad = {"type": "Polygon",
               "coordinates": [[[30.0, 50.0], [30.1, 50.0], [30.1, 50.1], [30.0, 50.0]]]}
        data = self.write_iso({"5": bad, "10": self.iso_geom(), "15": self.iso_geom()})
        report = validate_export(data, CONFIG_PATH)
        self.assertIn("outside_moscow", self.codes(report, "critical"))


if __name__ == "__main__":
    unittest.main()
