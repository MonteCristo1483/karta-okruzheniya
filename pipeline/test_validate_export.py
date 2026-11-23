"""Юнит-тесты валидатора выгрузки (HANDOFF §8).

Запуск из корня репозитория:
  pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v
"""

from __future__ import annotations

import copy
import gzip
import hashlib
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


def write_district_export(root: Path) -> Path:
    data = root / "new-data"
    district_id = 99
    district_dir = data / "districts" / str(district_id)
    district_dir.mkdir(parents=True)
    building_feature = globals()["building"](1, is_home=True)
    poi_features = default_poi()
    redzone_features = [redzone()]

    def write_json(path: Path, payload: dict) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path.write_bytes(content)
        return content

    def write_gzip_json(path: Path, payload: dict) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = gzip.compress(json.dumps(payload).encode("utf-8"), mtime=0)
        path.write_bytes(content)
        return content

    def entry(role: str, relative_path: str, content: bytes, shard: int | None = None) -> dict:
        result = {
            "role": role,
            "path": f"data/{relative_path}",
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if shard is not None:
            result["shard"] = shard
        return result

    manifest_files = []
    for filename, role, features in (
        ("buildings.geojson", "buildings_geojson", [building_feature]),
        ("poi.geojson", "poi_geojson", poi_features),
        ("redzones.geojson", "redzones_geojson", redzone_features),
        ("basemap.geojson", "basemap_geojson", []),
    ):
        payload = {"type": "FeatureCollection", "features": features}
        content = write_json(district_dir / filename, payload)
        if role in {"buildings_geojson", "basemap_geojson"}:
            manifest_files.append(
                entry(role, f"districts/{district_id}/{filename}", content)
            )

    for filename, role in (
        ("buildings.pmtiles", "buildings_pmtiles"),
        ("basemap.pmtiles", "basemap_pmtiles"),
    ):
        path = district_dir / filename
        path.write_bytes(b"tile")
        manifest_files.append(
            entry(role, f"districts/{district_id}/{filename}", path.read_bytes())
        )

    routes_dir = data / "routes" / str(district_id)
    isochrones_dir = data / "isochrones" / str(district_id)
    valid_route = {
        "1": {
            "categories": {
                "parks": {
                    "minutes": 1,
                    "distance_m": 10,
                    "geometry": {"type": "LineString", "coordinates": []},
                }
            }
        }
    }
    iso_geometry = {
        "type": "Polygon",
        "coordinates": [[[37.56, 55.76], [37.561, 55.76],
                         [37.561, 55.761], [37.56, 55.76]]],
    }
    for shard in range(16):
        route_content = write_gzip_json(
            routes_dir / f"{shard}.json.gz",
            valid_route if shard == 1 else {},
        )
        iso_content = write_gzip_json(
            isochrones_dir / f"{shard}.json.gz",
            {"1": {"5": iso_geometry, "10": iso_geometry, "15": iso_geometry}}
            if shard == 1
            else {},
        )
        manifest_files.append(
            entry("routes", f"routes/{district_id}/{shard}.json.gz", route_content, shard)
        )
        manifest_files.append(
            entry("isochrones", f"isochrones/{district_id}/{shard}.json.gz", iso_content, shard)
        )

    index_content = write_gzip_json(
        data / "index.json.gz",
        {"v": 1, "ids": [1], "lon": [37.56], "lat": [55.76], "addr": ["ул. Тестовая, 1"]},
    )
    index_meta = {
        "role": "index",
        "path": "data/index.json.gz",
        "bytes": len(index_content),
        "sha256": hashlib.sha256(index_content).hexdigest(),
    }
    write_json(data / "districts" / "manifest.json", {
        "v": 1,
        "index": index_meta,
        "districts": [{
            "id": district_id,
            "name": "Тестовый район",
            "bbox": [37.54, 55.74, 37.59, 55.79],
            "files": manifest_files,
        }],
    })
    (data / "buildings.pmtiles").write_bytes(b"PMTiles\x03city buildings")
    (data / "basemap.pmtiles").write_bytes(b"PMTiles\x03city basemap")
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


class TestDistrictExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = write_district_export(Path(self.tmp.name))

    def test_new_format_validates_manifest_index_and_gzip_shards(self):
        report = validate_export(self.data, CONFIG_PATH, "new")
        self.assertEqual(report.critical(), [])
        self.assertEqual(report.stats["districts"], 1)
        self.assertEqual(report.stats["search_index"], 1)
        self.assertEqual(report.stats["buildings"], 1)
        self.assertEqual(report.stats["route_files"], 16)
        self.assertEqual(report.stats["isochrone_files"], 16)

    def test_new_format_checks_sample_sha256(self):
        tile = self.data / "districts" / "99" / "buildings.pmtiles"
        tile.write_bytes(b"bad!")
        report = validate_export(self.data, CONFIG_PATH, "new")
        self.assertIn("sha_mismatch", {issue.code for issue in report.critical()})

    def test_new_format_rejects_invalid_city_pmtiles_header(self):
        (self.data / "basemap.pmtiles").write_bytes(b"not pmtiles")
        report = validate_export(self.data, CONFIG_PATH, "new")
        self.assertIn("bad_city_pmtiles", {issue.code for issue in report.critical()})

    def test_explicit_new_format_requires_manifest(self):
        report = validate_export(Path(self.tmp.name) / "missing", CONFIG_PATH, "new")
        self.assertIn("missing_manifest", {issue.code for issue in report.critical()})


if __name__ == "__main__":
    unittest.main()
