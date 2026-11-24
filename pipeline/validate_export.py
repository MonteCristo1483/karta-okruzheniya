#!/usr/bin/env python3
"""Валидация выгрузки для веб-карты (HANDOFF §8).

Читает готовые файлы `web/public/data/*.geojson` и `config/scoring.json`
и проверяет, что данные внутренне согласованы:

  * id зданий уникальны и положительны;
  * индекс в диапазоне из конфига;
  * разбивка сходится с индексом (база + бонусы + штрафы, с учётом клампа);
  * нет отрицательных расстояний/минут;
  * бонусы не больше максимума категории, штрафы не больше модуля максимума;
  * адрес не «16» без улицы, год не 0/1700/2050;
  * геометрия в границах Москвы;
  * нет дублей POI, типы POI известны конфигу;
  * типы красных зон и классов дорог есть в конфиге;
  * в маршрутах нет отрицательных минут/метров.

Критичные ошибки (кривые числа, дубли id, расхождение формулы) дают
exit code 1; предупреждения (покрытие, подозрительный год, дубль POI) —
только отчёт.

Использование:
  python pipeline/validate_export.py
  python pipeline/validate_export.py path/to/web/public/data
  python pipeline/validate_export.py --json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "web" / "public" / "data"
CONFIG_PATH = ROOT / "config" / "scoring.json"

# Пресня с запасом: выгрузка не должна вылезать за Москву.
MOSCOW_BBOX = (55.14, 36.80, 56.03, 37.98)  # вся Москва с ТиНАО (2026)
CURRENT_YEAR = 2026
NUMBER_ONLY_ADDRESS = re.compile(r"^\d+([/\-]\d+)?$")
MAX_ISSUES_PER_CODE = 200


@dataclass
class Issue:
    severity: str  # "critical" | "warning"
    code: str
    message: str
    building_id: int | None = None
    address: str | None = None


@dataclass
class Report:
    data_dir: Path
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)

    def add(self, severity: str, code: str, message: str,
            building_id: int | None = None, address: str | None = None) -> None:
        if sum(1 for i in self.issues if i.code == code) >= MAX_ISSUES_PER_CODE:
            return
        self.issues.append(Issue(severity, code, message, building_id, address))

    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "critical"]

    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


def dec(value) -> Decimal:
    return Decimal(str(value))


def round_half_up(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_features(path: Path, report: Report, name: str) -> list[dict]:
    if not path.exists():
        report.add("critical", "missing_file", f"нет файла {path.name}")
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("critical", "broken_json", f"{path.name}: {exc}")
        return []
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "FeatureCollection"
        or not isinstance(payload.get("features"), list)
    ):
        report.add("critical", "bad_collection", f"{path.name}: не FeatureCollection")
        return []
    features = payload["features"]
    report.stats[name] = len(features)
    return features


def iter_positions(geometry: dict):
    """Все координаты геометрии (любая глубина вложенности)."""
    def walk(node):
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                yield node
            else:
                for child in node:
                    yield from walk(child)
    yield from walk(geometry.get("coordinates", []))


def check_geometry(geometry: dict | None, report: Report, label: str,
                   building_id: int | None = None, margin: float = 0.0) -> None:
    if not isinstance(geometry, dict):
        report.add("critical", "bad_geometry", f"{label}: нет geometry", building_id)
        return
    if geometry.get("type") not in ("Polygon", "MultiPolygon", "LineString",
                                   "MultiLineString", "Point"):
        report.add("critical", "bad_geometry", f"{label}: тип {geometry.get('type')}",
                   building_id)
        return
    lat_min, lon_min, lat_max, lon_max = MOSCOW_BBOX
    # Линейные объекты (магистрали, ж/д) берутся целиком, поэтому у границы
    # города законно выходят за bbox на несколько километров — им разрешаем
    # margin, остальным 0 (иначе поедет проверка на битые координаты).
    lat_min, lon_min = lat_min - margin, lon_min - margin
    lat_max, lon_max = lat_max + margin, lon_max + margin
    for lon, lat, *_ in iter_positions(geometry):
        if not (math.isfinite(lon) and math.isfinite(lat)):
            report.add("critical", "bad_geometry", f"{label}: NaN/Inf в координатах",
                       building_id)
            return
        if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            report.add("critical", "outside_moscow",
                       f"{label}: координата {lat:.5f},{lon:.5f} вне Москвы",
                       building_id)
            return


def multiplier_for_minutes(minutes: Decimal, cfg: dict) -> Decimal:
    for threshold in sorted(cfg["bonuses"]["time_thresholds_min"], key=lambda t: t["up_to"]):
        if minutes <= dec(threshold["up_to"]):
            return dec(threshold["multiplier"])
    return Decimal(0)


def expected_penalty(name: str, entry: dict, cfg: dict) -> Decimal | None:
    """Пересчитать штраф из расстояния, веса и радиуса конфига."""
    penalty_cfg = cfg["penalties"].get(name)
    if penalty_cfg is None:
        return None
    radius = dec(penalty_cfg["radius_m"])
    distance = dec(entry["distance_m"])
    if distance >= radius:
        return Decimal(0)
    classes = penalty_cfg.get("classes")
    if isinstance(classes, dict):
        weight = dec(classes.get(entry.get("class"), 1))
    else:
        weight = Decimal(1)
    return dec(penalty_cfg["max"]) * weight * (1 - distance / radius)


def check_buildings(features: list[dict], cfg: dict, report: Report) -> None:
    base = dec(cfg["index"]["base"])
    low = dec(cfg["index"]["min"])
    high = dec(cfg["index"]["max"])
    places = int(cfg["index"]["rounding_decimals"])
    categories = cfg["bonuses"]["categories"]
    penalties = {k: v for k, v in cfg["penalties"].items() if isinstance(v, dict)}
    allowed_multipliers = {Decimal(0)} | {
        dec(t["multiplier"]) for t in cfg["bonuses"]["time_thresholds_min"]
    }
    seen_ids: set = set()
    with_address = with_year = with_height = 0

    for feature in features:
        props = feature.get("properties") or {}
        building_id = props.get("id")
        address = props.get("address")
        check_geometry(feature.get("geometry"), report,
                       f"здание {building_id}", building_id)
        if not isinstance(building_id, int) or building_id <= 0:
            report.add("critical", "bad_id", f"id={building_id!r} не положительное число",
                       address=address)
            building_id = None
        elif building_id in seen_ids:
            report.add("critical", "duplicate_id", f"id {building_id} повторяется",
                       building_id, address)
        else:
            seen_ids.add(building_id)

        if address:
            with_address += 1
            if NUMBER_ONLY_ADDRESS.match(str(address).strip()):
                report.add("warning", "address_without_street",
                           f"адрес без улицы: «{address}»", building_id, address)

        year = props.get("year")
        if year is not None:
            with_year += 1
            if not isinstance(year, int) or year <= 1700 or year > CURRENT_YEAR + 2:
                report.add("warning", "suspicious_year",
                           f"подозрительный год постройки: {year!r}", building_id, address)

        height = props.get("height")
        if height is not None:
            with_height += 1
            if not isinstance(height, (int, float)) or not math.isfinite(height) \
                    or height <= 0 or height > 400:
                report.add("warning", "suspicious_height",
                           f"подозрительная высота: {height!r}", building_id, address)

        # Нежилые здания присутствуют на карте, но для них не рассчитываются
        # индекс окружения и breakdown.
        if props.get("is_home") is False:
            continue

        index = props.get("index")
        if not isinstance(index, (int, float)):
            report.add("critical", "bad_index", f"index={index!r} не число",
                       building_id, address)
            index = None
        elif not (low <= dec(index) <= high):
            report.add("critical", "index_out_of_range",
                       f"индекс {index} вне {low}..{high}", building_id, address)

        breakdown = props.get("breakdown")
        if not isinstance(breakdown, dict):
            report.add("critical", "bad_breakdown", "нет breakdown", building_id, address)
            continue
        if dec(breakdown.get("base", 0)) != base:
            report.add("warning", "base_mismatch",
                       f"база {breakdown.get('base')} ≠ конфиг {base}",
                       building_id, address)

        total = dec(breakdown.get("base", 0))
        for name, entry in (breakdown.get("bonuses") or {}).items():
            cat_cfg = categories.get(name)
            if cat_cfg is None:
                report.add("warning", "unknown_bonus", f"неизвестная категория бонуса {name}",
                           building_id, address)
                continue
            if entry is None:
                report.add("warning", "empty_bonus", f"нет POI категории {name}",
                           building_id, address)
                continue
            bonus = dec(entry.get("bonus", 0))
            minutes = dec(entry.get("minutes", 0))
            distance = dec(entry.get("distance_m", 0))
            multiplier = dec(entry.get("multiplier", 0))
            total += bonus
            if bonus < 0:
                report.add("critical", "negative_bonus", f"бонус {name} < 0: {bonus}",
                           building_id, address)
            if bonus > dec(cat_cfg["max"]):
                report.add("critical", "bonus_too_big",
                           f"бонус {name} {bonus} > максимум {cat_cfg['max']}",
                           building_id, address)
            if minutes < 0 or distance < 0:
                report.add("critical", "negative_value",
                           f"{name}: минуты {minutes}, метры {distance}",
                           building_id, address)
            if multiplier not in allowed_multipliers:
                report.add("warning", "bad_multiplier",
                           f"{name}: множитель {multiplier} не из конфига",
                           building_id, address)
            expected = round_half_up(dec(cat_cfg["max"]) * multiplier, 3)
            if abs(round_half_up(bonus, 3) - expected) > Decimal("0.01"):
                report.add("warning", "bonus_mismatch",
                           f"{name}: бонус {bonus} ≠ max×множитель {expected}",
                           building_id, address)
            by_minutes = multiplier_for_minutes(round_half_up(minutes, 1), cfg)
            if by_minutes != multiplier:
                report.add("warning", "multiplier_mismatch",
                           f"{name}: {minutes} мин → множитель {by_minutes}, в данных {multiplier}",
                           building_id, address)

        for name, entry in (breakdown.get("penalties") or {}).items():
            pen_cfg = penalties.get(name)
            if pen_cfg is None:
                report.add("warning", "unknown_penalty", f"неизвестный штраф {name}",
                           building_id, address)
                continue
            if entry is None:
                report.add("warning", "empty_penalty", f"нет слоя {name}",
                           building_id, address)
                continue
            penalty = dec(entry.get("penalty", 0))
            distance = dec(entry.get("distance_m", 0))
            total += penalty
            if distance < 0:
                report.add("critical", "negative_value",
                           f"{name}: метры {distance}", building_id, address)
            if penalty > 0:
                report.add("critical", "positive_penalty",
                           f"штраф {name} положительный: {penalty}", building_id, address)
            if penalty < dec(pen_cfg["max"]):
                report.add("critical", "penalty_too_big",
                           f"штраф {name} {penalty} больше модуля {pen_cfg['max']}",
                           building_id, address)
            recomputed = expected_penalty(name, entry, cfg)
            if recomputed is not None and abs(dec(entry["penalty"]) - recomputed) > Decimal("0.01"):
                report.add("warning", "penalty_mismatch",
                           f"{name}: штраф {penalty} ≠ пересчёт {round_half_up(recomputed, 3)}",
                           building_id, address)

        if index is None:
            continue
        clamped = min(max(total, low), high)
        expected_index = round_half_up(clamped, places)
        if abs(expected_index - dec(index)) > Decimal("0.001"):
            report.add("critical", "sum_mismatch",
                       f"разбивка даёт {expected_index}, в данных {index}",
                       building_id, address)
        elif abs(clamped - dec(index)) > Decimal("0.06"):
            report.add("warning", "rounding_gap",
                       f"разбивка {total} → индекс {index} (расхождение округления)",
                       building_id, address)

    total = len(features)
    report.stats["buildings_with_address"] = with_address
    report.stats["buildings_with_year"] = with_year
    report.stats["buildings_with_height"] = with_height
    if total:
        report.coverage["address_pct"] = round(100 * with_address / total, 1)
        report.coverage["year_pct"] = round(100 * with_year / total, 1)
        report.coverage["height_pct"] = round(100 * with_height / total, 1)


def check_poi(features: list[dict], cfg: dict, report: Report) -> None:
    known_types = {
        poi_type
        for cat in cfg["bonuses"]["categories"].values()
        for poi_type in cat["poi_types"]
    }
    seen: set = set()
    counts: dict[str, int] = {}
    for feature in features:
        props = feature.get("properties") or {}
        poi_type = props.get("type")
        counts[poi_type] = counts.get(poi_type, 0) + 1
        if poi_type not in known_types:
            report.add("warning", "unknown_poi_type",
                       f"тип POI {poi_type!r} не используется в скоринге")
        key = (
            poi_type,
            props.get("subtype"),
            props.get("name"),
            json.dumps(feature.get("geometry", {}).get("coordinates"), sort_keys=True),
        )
        if key in seen:
            report.add("warning", "duplicate_poi",
                       f"дубль POI: {poi_type} «{props.get('name')}»")
        seen.add(key)
        check_geometry(feature.get("geometry"), report, f"POI {props.get('name')}")

    report.stats["poi_by_type"] = counts
    missing = sorted(known_types - set(counts))
    for poi_type in missing:
        report.add("warning", "missing_poi_type",
                   f"тип {poi_type} есть в конфиге, но не выгружен (бонус не начисляется)")


def check_redzones(features: list[dict], cfg: dict, report: Report) -> None:
    kinds = {k: v for k, v in cfg["penalties"].items() if isinstance(v, dict)}
    for feature in features:
        props = feature.get("properties") or {}
        kind = props.get("kind")
        pen_cfg = kinds.get(kind)
        if pen_cfg is None:
            report.add("warning", "unknown_redzone", f"неизвестный тип зоны {kind!r}")
        elif isinstance(pen_cfg.get("classes"), dict):
            cls = props.get("class")
            if cls not in pen_cfg["classes"]:
                report.add("warning", "unknown_road_class",
                           f"класс дороги {cls!r} не в весах конфига")
        check_geometry(feature.get("geometry"), report, f"зона {kind}", margin=0.1)


def check_routes(data_dir: Path, cfg: dict, report: Report) -> None:
    routes_dir = data_dir / "routes"
    if not routes_dir.is_dir():
        return
    files = sorted([*routes_dir.rglob("*.json"), *routes_dir.rglob("*.json.gz")])
    report.stats["route_files"] = len(files)
    categories = set(cfg["bonuses"]["categories"])
    for path in files:
        try:
            raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            payload = json.loads(raw)
        except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
            report.add("critical", "broken_route", f"{path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            report.add("critical", "broken_route", f"{path.name}: корень маршрута не объект")
            continue
        routes = [payload] if "categories" in payload else payload.values()
        for route in routes:
            if not isinstance(route, dict):
                report.add("critical", "broken_route", f"{path.name}: запись маршрута не объект")
                continue
            route_categories = route.get("categories") or {}
            if not isinstance(route_categories, dict):
                report.add("critical", "broken_route", f"{path.name}: categories не объект")
                continue
            for category, entry in route_categories.items():
                if not isinstance(entry, dict):
                    report.add("critical", "broken_route", f"{path.name}: категория {category} не объект")
                    continue
                if category not in categories:
                    report.add("warning", "unknown_route_category",
                               f"{path.name}: категория {category!r}")
                if dec(entry.get("minutes", 0)) < 0 or dec(entry.get("distance_m", 0)) < 0:
                    report.add("critical", "negative_route",
                               f"{path.name}: минуты {entry.get('minutes')}, "
                               f"метры {entry.get('distance_m')}")


def check_isochrones(data_dir: Path, report: Report) -> None:
    """Изохроны: файл на дом, три контура, геометрия в границах Москвы."""
    iso_dir = data_dir / "isochrones"
    if not iso_dir.is_dir():
        report.add("warning", "missing_isochrones", "нет папки isochrones")
        return
    files = sorted([*iso_dir.rglob("*.json"), *iso_dir.rglob("*.json.gz")])
    report.stats["isochrone_files"] = len(files)
    expected = {"5", "10", "15"}
    for path in files:
        try:
            raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            payload = json.loads(raw)
        except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
            report.add("critical", "broken_isochrone", f"{path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            report.add("critical", "broken_isochrone", f"{path.name}: корень изохрон не объект")
            continue
        buildings = payload.values() if path.suffix == ".gz" else [payload]
        for contours in buildings:
            if not isinstance(contours, dict) or set(contours) != expected:
                keys = sorted(contours) if isinstance(contours, dict) else []
                report.add("warning", "bad_contours",
                           f"{path.name}: контуры {keys}")
                continue
            for minutes, geometry in contours.items():
                if not isinstance(geometry, dict) or geometry.get("type") not in (
                    "Polygon", "MultiPolygon",
                ):
                    report.add("critical", "bad_isochrone_geometry",
                               f"{path.name}: {minutes} мин — {type(geometry).__name__}")
                    continue
                check_geometry(geometry, report, f"изохрона {path.stem}, {minutes} мин")


def manifest_file_path(data_dir: Path, manifest_path: object) -> Path | None:
    if not isinstance(manifest_path, str):
        return None
    parts = Path(manifest_path).parts
    if not parts or parts[0] != "data" or ".." in parts or Path(manifest_path).is_absolute():
        return None
    return data_dir.joinpath(*parts[1:])


def validate_district_export(data_dir: Path, cfg: dict, report: Report) -> Report:
    manifest_path = data_dir / "districts" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("critical", "bad_manifest", f"manifest.json: {exc}")
        return report

    if (
        not isinstance(manifest, dict)
        or manifest.get("v") != 1
        or not isinstance(manifest.get("districts"), list)
    ):
        report.add("critical", "bad_manifest", "неподдерживаемая версия или нет districts")
        return report
    districts = manifest["districts"]
    report.stats["districts"] = len(districts)
    seen_districts: set[int] = set()
    all_buildings: list[dict] = []
    district_buildings: list[list[dict]] = []
    all_poi: list[dict] = []
    all_redzones: list[dict] = []
    home_ids: set[int] = set()
    # С V2-S3b per-district PMTiles не собираются: здания и подложка — один
    # городской файл в корне data/ (проверяется ниже). Обязателен только
    # buildings.geojson района.
    required_roles = {
        "buildings_geojson",
    }
    sample_hashes: set[Path] = set()

    index_meta = manifest.get("index")
    if not isinstance(index_meta, dict):
        index_meta = {}
    index_path = manifest_file_path(data_dir, index_meta.get("path"))
    if index_path is None or not index_path.is_file():
        report.add("critical", "missing_index", "нет data/index.json.gz")
    else:
        if index_meta.get("bytes") != index_path.stat().st_size:
            report.add("critical", "bad_file_size", "index.json.gz: размер не совпадает с манифестом")
        sample_hashes.add(index_path)

    for district in districts:
        if not isinstance(district, dict):
            report.add("critical", "bad_district", "описание района должно быть объектом")
            continue
        district_id = district.get("id")
        if not isinstance(district_id, int) or district_id <= 0:
            report.add("critical", "bad_district", f"некорректный id района: {district_id!r}")
            continue
        if district_id in seen_districts:
            report.add("critical", "duplicate_district", f"id района повторяется: {district_id}")
        seen_districts.add(district_id)
        bbox = district.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(value, (int, float)) for value in bbox)
            or bbox[0] >= bbox[2]
            or bbox[1] >= bbox[3]
        ):
            report.add("critical", "bad_district_bbox", f"район {district_id}: некорректный bbox")

        files = district.get("files")
        if not isinstance(files, list):
            report.add("critical", "bad_district_files", f"район {district_id}: нет files")
            continue
        entries_by_role: dict[str, list[dict]] = {}
        for entry in files:
            if not isinstance(entry, dict):
                report.add("critical", "bad_manifest_file", f"район {district_id}: некорректный файл")
                continue
            role = entry.get("role")
            if not isinstance(role, str):
                report.add("critical", "bad_manifest_file", f"район {district_id}: нет role")
                continue
            entries_by_role.setdefault(role, []).append(entry)
            path = manifest_file_path(data_dir, entry.get("path"))
            if path is None:
                report.add("critical", "bad_manifest_path", f"район {district_id}: путь вне data/")
                continue
            if not path.is_file():
                report.add("critical", "missing_file", f"нет файла {entry.get('path')}")
                continue
            expected_bytes = entry.get("bytes")
            if isinstance(expected_bytes, int) and expected_bytes != path.stat().st_size:
                report.add("critical", "bad_file_size", f"{entry.get('path')}: размер не совпадает")
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                report.add("critical", "bad_manifest_sha", f"{entry.get('path')}: нет sha256")
            elif (
                role in {"buildings_geojson", "buildings_pmtiles", "basemap_geojson", "basemap_pmtiles"}
                or (role in {"routes", "isochrones"} and entry.get("shard") == 0)
            ):
                sample_hashes.add(path)

        for role in required_roles:
            if len(entries_by_role.get(role, [])) != 1:
                report.add(
                    "critical",
                    "bad_manifest_roles",
                    f"район {district_id}: ожидался один файл {role}",
                )
        for role in ("routes", "isochrones"):
            shards = {
                entry.get("shard")
                for entry in entries_by_role.get(role, [])
                if isinstance(entry.get("shard"), int)
            }
            if shards != set(range(16)):
                report.add(
                    "critical",
                    "bad_manifest_shards",
                    f"район {district_id}: {role} должны содержать шарды 0–15",
                )

        building_entries = entries_by_role.get("buildings_geojson", [])
        if building_entries:
            buildings = load_features(
                manifest_file_path(data_dir, building_entries[0].get("path")) or Path(),
                report,
                f"buildings_{district_id}",
            )
            for feature in buildings:
                props = feature.get("properties") or {}
                if not isinstance(props.get("is_home"), bool):
                    report.add(
                        "critical",
                        "bad_home_flag",
                        f"здание {props.get('id')}: is_home должен быть boolean",
                        props.get("id") if isinstance(props.get("id"), int) else None,
                    )
                elif props["is_home"] and isinstance(props.get("id"), int):
                    home_ids.add(props["id"])
            all_buildings.extend(buildings)
            district_buildings.append(buildings)

        for filename, target, label in (
            ("poi.geojson", all_poi, f"poi_{district_id}"),
            ("redzones.geojson", all_redzones, f"redzones_{district_id}"),
        ):
            layer_path = data_dir / "districts" / str(district_id) / filename
            target.extend(load_features(layer_path, report, label))

    for city_file in ("buildings.pmtiles", "basemap.pmtiles"):
        path = data_dir / city_file
        if not path.is_file() or path.stat().st_size == 0:
            report.add("critical", "missing_city_pmtiles", f"нет или пустой data/{city_file}")
            continue
        with path.open("rb") as fh:
            header = fh.read(8)
        if header[:7] != b"PMTiles" or len(header) < 8 or header[7] != 3:
            report.add("critical", "bad_city_pmtiles", f"data/{city_file}: неверный PMTiles v3 header")

    for path in sample_hashes:
        expected = None
        if path == index_path:
            expected = index_meta.get("sha256")
        else:
            for district in districts:
                if not isinstance(district, dict):
                    continue
                for entry in district.get("files") or []:
                    if manifest_file_path(data_dir, entry.get("path")) == path:
                        expected = entry.get("sha256")
                        break
                if expected:
                    break
        if not isinstance(expected, str):
            report.add("critical", "bad_manifest_sha", f"{path.name}: нет sha256")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            report.add("critical", "sha_mismatch", f"{path.name}: sha256 не совпадает")

    if index_path is not None and index_path.is_file():
        try:
            index = json.loads(gzip.decompress(index_path.read_bytes()))
            if not isinstance(index, dict):
                raise ValueError("корень индекса должен быть объектом")
            columns = ("ids", "lon", "lat", "addr")
            lengths = [len(index.get(column, [])) for column in columns]
            if (
                index.get("v") != 1
                or len(set(lengths)) != 1
                or lengths[0] == 0
                or len(set(index["ids"])) != lengths[0]
            ):
                raise ValueError("неверная версия, пустые или расходящиеся колонки")
            indexed_ids = set(index["ids"])
            if indexed_ids != home_ids:
                report.add(
                    "critical",
                    "index_home_mismatch",
                    f"индекс содержит {len(indexed_ids)} id, жилых домов {len(home_ids)}",
                )
            report.stats["search_index"] = lengths[0]
        except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            report.add("critical", "bad_search_index", f"index.json.gz: {exc}")

    report.stats["buildings"] = len(all_buildings)
    report.stats["poi"] = len(all_poi)
    report.stats["redzones"] = len(all_redzones)
    building_stats = {
        "buildings_with_address": 0,
        "buildings_with_year": 0,
        "buildings_with_height": 0,
    }
    for features in district_buildings:
        district_report = Report(data_dir=data_dir)
        check_buildings(features, cfg, district_report)
        report.issues.extend(district_report.issues)
        for key in building_stats:
            building_stats[key] += district_report.stats.get(key, 0)
    report.stats.update(building_stats)
    total_buildings = len(all_buildings)
    if total_buildings:
        report.coverage.update({
            "address_pct": round(100 * building_stats["buildings_with_address"] / total_buildings, 1),
            "year_pct": round(100 * building_stats["buildings_with_year"] / total_buildings, 1),
            "height_pct": round(100 * building_stats["buildings_with_height"] / total_buildings, 1),
        })
    check_poi(all_poi, cfg, report)
    check_redzones(all_redzones, cfg, report)
    check_routes(data_dir, cfg, report)
    check_isochrones(data_dir, report)
    return report


def validate_export(
    data_dir: Path,
    config_path: Path = CONFIG_PATH,
    export_format: str = "auto",
) -> Report:
    cfg = load_config(config_path)
    report = Report(data_dir=data_dir)
    has_manifest = (data_dir / "districts" / "manifest.json").is_file()
    if export_format == "new" and not has_manifest:
        report.add("critical", "missing_manifest", "нет districts/manifest.json")
        return report
    if export_format == "new" or (export_format == "auto" and has_manifest):
        return validate_district_export(data_dir, cfg, report)
    buildings = load_features(data_dir / "presnya_buildings.geojson", report, "buildings")
    poi = load_features(data_dir / "presnya_poi.geojson", report, "poi")
    redzones = load_features(data_dir / "presnya_redzones.geojson", report, "redzones")
    check_buildings(buildings, cfg, report)
    check_poi(poi, cfg, report)
    check_redzones(redzones, cfg, report)
    check_routes(data_dir, cfg, report)
    check_isochrones(data_dir, report)
    return report


def format_report(report: Report, details: int = 5) -> str:
    stats = report.stats
    lines = [f"Выгрузка: {report.data_dir}"]
    if "districts" in stats:
        lines.append(f"Районов {stats.get('districts', 0)} · индекс поиска {stats.get('search_index', 0)}")
    lines.append(
        "Зданий {buildings} · POI {poi} · зон {redzones} · маршрутов {route_files} · изохрон {isochrone_files}".format(
            buildings=stats.get("buildings", 0),
            poi=stats.get("poi", 0),
            redzones=stats.get("redzones", 0),
            route_files=stats.get("route_files", 0),
            isochrone_files=stats.get("isochrone_files", 0),
        )
    )
    coverage = report.coverage
    if coverage:
        lines.append(
            "Покрытие: адрес {address_pct}% · год {year_pct}% · высота {height_pct}%".format(
                **coverage
            )
        )
    by_type = stats.get("poi_by_type") or {}
    if by_type:
        lines.append("POI: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())))

    critical = report.critical()
    warnings = report.warnings()
    lines.append(f"Критичных: {len(critical)} · предупреждений: {len(warnings)}")

    for title, issues in (("КРИТИЧНО", critical), ("ПРЕДУПРЕЖДЕНИЯ", warnings)):
        if not issues:
            continue
        lines.append("")
        lines.append(title + ":")
        by_code: dict[str, list[Issue]] = {}
        for issue in issues:
            by_code.setdefault(issue.code, []).append(issue)
        for code, group in by_code.items():
            for issue in group[:details]:
                where = ""
                if issue.building_id is not None:
                    where = f" [id {issue.building_id}]"
                if issue.address:
                    where += f" «{issue.address}»"
                lines.append(f"  {issue.message}{where}")
            if len(group) > details:
                lines.append(f"  … и ещё {len(group) - details} с кодом {code}")

    lines.append("")
    lines.append("ОК: критичных ошибок нет" if not critical else "ЕСТЬ КРИТИЧНЫЕ ОШИБКИ")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows-консоль по умолчанию cp1251 и не печатает ×/→ — переключаемся
    # на UTF-8 с заменой непечатаемых символов, чтобы отчёт не падал.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # подменённый поток (тесты) или уже закрытый

    parser = argparse.ArgumentParser(description="Валидация выгрузки веб-карты")
    parser.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA_DIR),
                        help="папка с *.geojson (по умолчанию web/public/data)")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--format", choices=("auto", "legacy", "new"), default="auto")
    parser.add_argument("--details", type=int, default=5,
                        help="сколько примеров показывать на каждый код ошибки")
    parser.add_argument("--json", action="store_true", help="отчёт в JSON")
    args = parser.parse_args(argv)

    report = validate_export(Path(args.data_dir), Path(args.config), args.format)
    if args.json:
        payload = {
            "data_dir": str(report.data_dir),
            "stats": report.stats,
            "coverage": report.coverage,
            "issues": [issue.__dict__ for issue in report.issues],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(report, args.details))
    return 1 if report.critical() else 0


if __name__ == "__main__":
    sys.exit(main())
