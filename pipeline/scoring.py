#!/usr/bin/env python3
"""Скоринг «Индекс окружения» (SPEC §3). Все коэффициенты — из config/scoring.json.

Пешее время до POI: если для здания есть маршрут в таблице routes (V2,
Valhalla) — берём его минуты и длину; иначе считаем по прямой:
мин = м / (walking_speed_kmh × 1000 / 60). Штраф начисляется только при
расстоянии < радиуса слоя. Слои с enabled: false пропускаются.

Источники штрафов (config: penalties.*.source):
  linear — ближайшая линия из linear (kind = названию штрафа, веса по classes);
  zones  — ближайший полигон из zones (kind = названию штрафа, без весов).

Балл считается только жилым (is_home = TRUE). Нежилые здания остаются на
карте, но балла не получают.

Использование:
  python scoring.py             — жилые здания buildings_presnya
  python scoring.py --new-moscow — новые жилые дома Москвы, которых нет в scores
  python scoring.py 123 456 ... — конкретные жилые osm_id из buildings (для T12)
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

import scoring_v2

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "scoring.json"
from db import DB_DSN
BATCH_SIZE = 5_000
# Осторожный знаменатель для bbox-префильтра: на широте Москвы 1° долготы
# ≈ 62 км, поэтому 60 км/° не даёт пропустить объект восточнее/западнее.
METERS_PER_DEGREE = 60_000.0
# UTM 37N: метрическая проекция для Москвы, чтобы мерить max до полигона в м.
MOSCOW_UTM_SRID = 32637

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scores (
    building_id BIGINT PRIMARY KEY,
    index       NUMERIC(4, 1) NOT NULL,
    breakdown   JSONB NOT NULL
)
"""

UPSERT = """
INSERT INTO scores (building_id, index, breakdown)
VALUES (%s, %s, %s)
ON CONFLICT (building_id) DO UPDATE
SET index = EXCLUDED.index, breakdown = EXCLUDED.breakdown
"""

BUILDING_IDS = "SELECT osm_id FROM {scope}"

NEAREST_POI = """
SELECT b.osm_id, ST_Distance(b.geometry::geography, p.geometry::geography)
FROM {scope} b
CROSS JOIN LATERAL (
    SELECT p.geometry FROM poi p
    WHERE p.type = ANY(%s) {subtype_clause}
    ORDER BY p.geometry <-> b.geometry
    LIMIT 1
) p
"""

NEAREST_LINEAR = """
SELECT b.osm_id,
       ST_Distance(b.geometry::geography, l.geometry::geography),
       l.class
FROM {scope} b
CROSS JOIN LATERAL (
    SELECT l.geometry, l.class FROM linear l
    WHERE l.kind = %s AND l.class = ANY(%s)
    ORDER BY l.geometry <-> b.geometry
    LIMIT 1
) l
"""

NEAREST_ZONE = """
SELECT b.osm_id,
       ST_Distance(b.geometry::geography, z.geometry::geography),
       z.kind
FROM {scope} b
CROSS JOIN LATERAL (
    SELECT z.geometry, z.kind FROM zones z
    WHERE z.kind = %s
    ORDER BY z.geometry <-> b.geometry
    LIMIT 1
) z
"""

# --- v2 (docs/METHODOLOGY-V2.md) -------------------------------------------
# Транспорт: ближайшее расстояние по каждому классу дороги в пределах его R.
V2_TRANSPORT = """
SELECT b.osm_id, l.class,
       MIN(ST_Distance(b.geometry::geography, l.geometry::geography))
FROM {scope} b
JOIN linear l
  ON l.kind = 'road'
 AND l.class = ANY(%s)
 AND l.geometry && ST_Expand(b.geometry, %s)
 AND ST_Distance(b.geometry::geography, l.geometry::geography) <= %s
GROUP BY b.osm_id, l.class
"""

# Зелень: парки-полигоны >= min_area, целиком в радиусе (max d до вершин <= R).
# near_m — геодезическое расстояние; far_m — максимальное расстояние до
# полигона в метрах через UTM 37N (Москва), чтобы не гонять вершины в цикле.
V2_GREEN = """
SELECT b.osm_id, g.area_m2,
       ST_Distance(b.geometry::geography, g.geometry::geography) AS near_m,
       ST_MaxDistance(ST_Transform(b.geometry, {srid}),
                      ST_Transform(g.geometry, {srid})) AS far_m
FROM {scope} b
JOIN green_areas g
  ON g.area_m2 >= %s
 AND g.geometry && ST_Expand(b.geometry, %s)
WHERE ST_DWithin(b.geometry::geography, g.geometry::geography, %s)
  AND ST_MaxDistance(ST_Transform(b.geometry, {srid}),
                     ST_Transform(g.geometry, {srid})) <= %s
"""

# Дом: ближайшая запись реестра (год, серия, материал) в радиусе match_radius.
V2_HOME = """
SELECT b.osm_id, y.building_year, y.series, y.material
FROM {scope} b
LEFT JOIN LATERAL (
    SELECT y.building_year, y.series, y.material
    FROM building_years y
    WHERE y.geom && ST_Expand(b.geometry, %s)
      AND ST_DistanceSphere(ST_PointOnSurface(b.geometry), y.geom) <= %s
    ORDER BY y.geom <-> b.geometry
    LIMIT 1
) y ON TRUE
"""

# Плотность: жилая площадь (footprint * этажность) соседей в радиусе.
V2_DENSITY = """
SELECT b.osm_id,
       SUM(ST_Area(n.geometry::geography)
           * COALESCE(GREATEST(1, ROUND(h.height / 3)), 1))
FROM {scope} b
JOIN buildings n
  ON n.is_home
 AND n.geometry && ST_Expand(b.geometry, %s)
 AND ST_Distance(n.geometry::geography, b.geometry::geography) <= %s
LEFT JOIN buildings_with_height h ON h.osm_id = n.osm_id
GROUP BY b.osm_id
"""


def dec(value) -> Decimal:
    return Decimal(str(value))


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def round_dec(value: Decimal, places: int = 1) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def walking_minutes(distance_m: Decimal, speed_kmh: Decimal) -> Decimal:
    return distance_m / (speed_kmh * 1000 / 60)


def multiplier_for(minutes: Decimal, thresholds: list[dict]) -> Decimal:
    for threshold in sorted(thresholds, key=lambda t: t["up_to"]):
        if minutes <= dec(threshold["up_to"]):
            return dec(threshold["multiplier"])
    return Decimal(0)


def bonus_for_minutes(
    minutes: Decimal, distance_m: Decimal, category_max: Decimal, cfg: dict
) -> dict:
    """Бонус категории за готовое пешее время (маршрут Valhalla)."""
    multiplier = multiplier_for(minutes, cfg["bonuses"]["time_thresholds_min"])
    return {
        "distance_m": float(round_dec(distance_m)),
        "minutes": float(round_dec(minutes)),
        "multiplier": float(multiplier),
        "bonus": float(category_max * multiplier),
    }


def bonus_for_distance(distance_m: Decimal, category_max: Decimal, cfg: dict) -> dict:
    """Бонус категории за расстояние до ближайшего POI (по прямой)."""
    minutes = walking_minutes(distance_m, dec(cfg["bonuses"]["walking_speed_kmh"]))
    return bonus_for_minutes(minutes, distance_m, category_max, cfg)


def penalty_for_distance(
    distance_m: Decimal,
    penalty_max: Decimal,
    weight: Decimal,
    radius_m: Decimal,
) -> Decimal:
    """Штраф за близость линии/зоны; 0 на границе радиуса и дальше."""
    if distance_m >= radius_m:
        return Decimal(0)
    return penalty_max * weight * (1 - distance_m / radius_m)


def finalize_index(total: Decimal, cfg: dict) -> Decimal:
    """Кламп в диапазон индекса и округление до десятых."""
    low = dec(cfg["index"]["min"])
    high = dec(cfg["index"]["max"])
    decimals = int(cfg["index"]["rounding_decimals"])
    return round_dec(clamp(total, low, high), decimals)


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def scope_from_args(argv: list[str]) -> tuple[str, list]:
    if argv == ["--new-moscow"]:
        # HOMES-V2: только новые жилые дома Москвы, которых ещё нет в scores.
        return (
            "(SELECT b.* FROM buildings b "
            "JOIN moscow m ON ST_Intersects(b.geometry, m.geometry) "
            "WHERE b.is_home AND b.osm_id NOT IN (SELECT building_id FROM scores))",
            [],
        )
    if not argv:
        return "(SELECT * FROM buildings_presnya WHERE is_home)", []
    ids = [int(a) for a in argv]
    return "(SELECT * FROM buildings WHERE osm_id = ANY(%s) AND is_home)", [ids]


def collect_bonuses(cur, scope: str, scope_params: list, cfg: dict, routes_map: dict) -> dict:
    bonuses: dict[str, dict] = {}
    for category, cat_cfg in cfg["bonuses"]["categories"].items():
        subtypes = cat_cfg.get("subtypes")
        subtype_clause = "AND p.subtype = ANY(%s)" if subtypes else ""
        params = [*scope_params, cat_cfg["poi_types"]]
        if subtypes:
            params.append(subtypes)
        cur.execute(NEAREST_POI.format(scope=scope, subtype_clause=subtype_clause), params)
        category_max = dec(cat_cfg["max"])
        per_building: dict[int, dict] = {}
        for osm_id, dist in cur.fetchall():
            route = routes_map.get((osm_id, category))
            if route is not None:
                minutes, distance_m = route
                per_building[osm_id] = bonus_for_minutes(
                    minutes, distance_m, category_max, cfg
                )
            else:
                per_building[osm_id] = bonus_for_distance(dec(dist), category_max, cfg)
        bonuses[category] = per_building
    return bonuses


def collect_penalties(cur, scope: str, scope_params: list, cfg: dict) -> tuple[dict, list]:
    penalties: dict[str, dict] = {}
    skipped: list[str] = []
    for name, pen_cfg in cfg["penalties"].items():
        if not isinstance(pen_cfg, dict):
            continue
        if not pen_cfg.get("enabled", False):
            skipped.append(name)
            continue
        classes = pen_cfg.get("classes")
        if isinstance(classes, dict):
            weight_of = {cls: dec(w) for cls, w in classes.items()}
        else:
            weight_of = {}
        if pen_cfg.get("source", "linear") == "zones":
            cur.execute(NEAREST_ZONE.format(scope=scope), [*scope_params, name])
        else:
            class_list = list(classes) if classes else []
            cur.execute(
                NEAREST_LINEAR.format(scope=scope),
                [*scope_params, name, class_list],
            )
        radius = dec(pen_cfg["radius_m"])
        penalty_max = dec(pen_cfg["max"])
        per_building: dict[int, dict] = {}
        for osm_id, dist, cls in cur.fetchall():
            distance = dec(dist)
            weight = weight_of.get(cls, Decimal(1))
            penalty = penalty_for_distance(distance, penalty_max, weight, radius)
            per_building[osm_id] = {
                "distance_m": float(round_dec(distance)),
                "class": cls,
                "weight": float(weight),
                "penalty": float(round_dec(penalty, 3)),
            }
        penalties[name] = per_building
    return penalties, skipped


def collect_v2_transport(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    transport = cfg["v2"]["transport"]
    classes = list(transport["classes"])
    max_radius = max(dec(spec["radius_m"]) for spec in transport["classes"].values())
    pad = float(max_radius) / METERS_PER_DEGREE * 1.2
    cur.execute(
        V2_TRANSPORT.format(scope=scope),
        [*scope_params, classes, pad, float(max_radius) + 1],
    )
    per_building: dict[int, dict[str, float]] = {}
    for osm_id, cls, distance in cur.fetchall():
        per_building.setdefault(osm_id, {})[cls] = float(distance)
    return {
        osm_id: scoring_v2.best_transport_quality(per_class, cfg)
        for osm_id, per_class in per_building.items()
    }


def collect_v2_green(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    green = cfg["v2"]["green"]
    radius = float(green["radius_m"])
    min_area = float(green["min_area_ha"]) * 10_000.0
    pad = radius / METERS_PER_DEGREE * 1.2
    srid = MOSCOW_UTM_SRID
    cur.execute(
        V2_GREEN.format(scope=scope, srid=srid),
        [*scope_params, min_area, pad, radius, radius],
    )
    per_building: dict[int, list[dict]] = {}
    for osm_id, area_m2, near_m, far_m in cur.fetchall():
        per_building.setdefault(osm_id, []).append({
            "area_m2": float(area_m2),
            "distance_m": float(near_m),
            "max_distance_m": float(far_m),
        })
    return {
        osm_id: scoring_v2.green_quality(parks, cfg)
        for osm_id, parks in per_building.items()
    }


def collect_v2_access(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    """Ближайший POI каждого типа доступности; отсутствующий тип -> missing."""
    types = cfg["v2"]["access"]["types"]
    per_building: dict[int, dict[str, float]] = {}
    for name, spec in types.items():
        subtypes = spec.get("subtypes")
        subtype_clause = "AND p.subtype = ANY(%s)" if subtypes else ""
        params = [*scope_params, spec["poi_types"]]
        if subtypes:
            params.append(subtypes)
        cur.execute(NEAREST_POI.format(scope=scope, subtype_clause=subtype_clause), params)
        for osm_id, dist in cur.fetchall():
            per_building.setdefault(osm_id, {})[name] = float(dist)
    return {
        osm_id: scoring_v2.access_quality(
            {name: distances.get(name) for name in types}, cfg
        )
        for osm_id, distances in per_building.items()
    }


def collect_v2_home(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    radius = float(cfg["v2"]["home"]["match_radius_m"])
    pad = radius / METERS_PER_DEGREE * 1.5
    cur.execute(V2_HOME.format(scope=scope), [*scope_params, pad, radius])
    return {
        osm_id: scoring_v2.home_quality(year, series, material, cfg)
        for osm_id, year, series, material in cur.fetchall()
    }


def collect_v2_density(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    radius = float(cfg["v2"]["density"]["radius_m"])
    pad = radius / METERS_PER_DEGREE * 1.5
    cur.execute(V2_DENSITY.format(scope=scope), [*scope_params, pad, radius])
    per_building: dict[int, dict] = {}
    for osm_id, floor_area in cur.fetchall():
        density = scoring_v2.density_from_floor_area(floor_area, radius)
        per_building[osm_id] = scoring_v2.density_pressure(density, cfg)
    return per_building


def run(scope: str, scope_params: list, cfg: dict) -> int:
    """Посчитать и сохранить индексы для скоупа (SQL-фрагмент FROM)."""
    base = dec(cfg["index"]["base"])

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(BUILDING_IDS.format(scope=scope), scope_params)
        building_ids = [row[0] for row in cur.fetchall()]

        # Готовые пешие маршруты (Valhalla): (building_id, category) ->
        # (минуты, длина_м). Зданий без маршрута — прямая (фолбэк).
        routes_map: dict = {}
        if building_ids:
            cur.execute(
                "SELECT building_id, category, minutes, distance_m "
                "FROM routes WHERE building_id = ANY(%s)",
                [building_ids],
            )
            routes_map = {
                (bid, cat): (dec(mins), dec(dist))
                for bid, cat, mins, dist in cur.fetchall()
            }

        bonuses = collect_bonuses(cur, scope, scope_params, cfg, routes_map)
        penalties, skipped = collect_penalties(cur, scope, scope_params, cfg)

        v2_enabled = bool(cfg.get("v2", {}).get("enabled", False))
        if v2_enabled:
            v2_transport = collect_v2_transport(cur, scope, scope_params, cfg)
            v2_access = collect_v2_access(cur, scope, scope_params, cfg)
            v2_green = collect_v2_green(cur, scope, scope_params, cfg)
            v2_home = collect_v2_home(cur, scope, scope_params, cfg)
            v2_density = collect_v2_density(cur, scope, scope_params, cfg)

        # Нет дороги в пределах максимального R — это известный ноль, не unknown.
        empty_transport = {"value": 0.0, "status": "outside", "class": None}
        empty_access = scoring_v2.access_quality(
            {name: None for name in cfg["v2"]["access"]["types"]}, cfg
        )
        empty_green = scoring_v2.green_quality([], cfg)
        empty_home = scoring_v2.home_quality(None, None, None, cfg)
        empty_density = scoring_v2.density_pressure(None, cfg)

        buffer: list[tuple] = []
        for osm_id in building_ids:
            breakdown: dict = {"base": float(base), "bonuses": {}, "penalties": {}}
            total = base
            for category, per_building in bonuses.items():
                entry = per_building.get(osm_id)
                breakdown["bonuses"][category] = entry
                total += dec(entry["bonus"]) if entry else Decimal(0)
            for name, per_building in penalties.items():
                entry = per_building.get(osm_id)
                breakdown["penalties"][name] = entry
                total += dec(entry["penalty"]) if entry else Decimal(0)
            index = finalize_index(total, cfg)
            if v2_enabled:
                v2_block = scoring_v2.build_v2_block(
                    v2_transport.get(osm_id, empty_transport),
                    v2_access.get(osm_id, empty_access),
                    v2_green.get(osm_id, empty_green),
                    v2_home.get(osm_id, empty_home),
                    v2_density.get(osm_id, empty_density),
                    cfg,
                )
                breakdown["version"] = 2
                breakdown["v2"] = v2_block
                index = dec(v2_block["index"])
            breakdown["index"] = float(index)
            buffer.append((osm_id, index, Jsonb(breakdown)))
            if len(buffer) >= BATCH_SIZE:
                cur.executemany(UPSERT, buffer)
                buffer.clear()
        if buffer:
            cur.executemany(UPSERT, buffer)
        conn.commit()

    print(
        f"Готово: {len(building_ids):,} зданий, слои disabled пропущены: "
        f"{', '.join(skipped) if skipped else 'нет'}"
    )
    return len(building_ids)


def main(argv: list[str]) -> int:
    cfg = load_config()
    scope, scope_params = scope_from_args(argv)
    run(scope, scope_params, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
