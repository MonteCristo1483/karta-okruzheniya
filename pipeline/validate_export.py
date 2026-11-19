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
MOSCOW_BBOX = (55.4, 37.2, 55.95, 37.9)
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
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
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
                   building_id: int | None = None) -> None:
    if not isinstance(geometry, dict):
        report.add("critical", "bad_geometry", f"{label}: нет geometry", building_id)
        return
    if geometry.get("type") not in ("Polygon", "MultiPolygon", "LineString",
                                   "MultiLineString", "Point"):
        report.add("critical", "bad_geometry", f"{label}: тип {geometry.get('type')}",
                   building_id)
        return
    lat_min, lon_min, lat_max, lon_max = MOSCOW_BBOX
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
        check_geometry(feature.get("geometry"), report, f"зона {kind}")


def check_routes(data_dir: Path, cfg: dict, report: Report) -> None:
    routes_dir = data_dir / "routes"
    if not routes_dir.is_dir():
        return
    files = list(routes_dir.glob("*.json"))
    report.stats["route_files"] = len(files)
    categories = set(cfg["bonuses"]["categories"])
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.add("critical", "broken_route", f"{path.name}: {exc}")
            continue
        for category, entry in (payload.get("categories") or {}).items():
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
    files = list(iso_dir.glob("*.json"))
    report.stats["isochrone_files"] = len(files)
    expected = {"5", "10", "15"}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.add("critical", "broken_isochrone", f"{path.name}: {exc}")
            continue
        if set(payload) != expected:
            report.add("warning", "bad_contours",
                       f"{path.name}: контуры {sorted(payload)}")
            continue
        for minutes, geometry in payload.items():
            if not isinstance(geometry, dict) or geometry.get("type") not in (
                "Polygon", "MultiPolygon",
            ):
                report.add("critical", "bad_isochrone_geometry",
                           f"{path.name}: {minutes} мин — {type(geometry).__name__}")
                continue
            check_geometry(geometry, report, f"изохрона {path.stem}, {minutes} мин")


def validate_export(data_dir: Path, config_path: Path = CONFIG_PATH) -> Report:
    cfg = load_config(config_path)
    report = Report(data_dir=data_dir)
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
    parser = argparse.ArgumentParser(description="Валидация выгрузки веб-карты")
    parser.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA_DIR),
                        help="папка с *.geojson (по умолчанию web/public/data)")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--details", type=int, default=5,
                        help="сколько примеров показывать на каждый код ошибки")
    parser.add_argument("--json", action="store_true", help="отчёт в JSON")
    args = parser.parse_args(argv)

    report = validate_export(Path(args.data_dir), Path(args.config))
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
