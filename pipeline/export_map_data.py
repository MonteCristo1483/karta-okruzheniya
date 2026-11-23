#!/usr/bin/env python3
"""Экспорт данных района для веб-карты (M1, M4) + переиспользуемое ядро.

По умолчанию (без аргументов) собирает Пресню из вьюх buildings_presnya /
poi_presnya / linear_presnya в web/public/data/:
  presnya_buildings.geojson — здания: геометрия, высота, index + разбивка, адрес;
  presnya_poi.geojson       — POI (type, subtype, name), магазины — только
                              subtype из config/scoring.json (как в скоринге);
                              транспорт: subway_entrance + rail_platform (МЦД/электрички).
  presnya_redzones.geojson  — «красные зоны»: линии магистралей и ж/д
                              (linear) + полигоны строек и промзон (zones).
  routes/<id>.json          — маршруты выбранного дома (V2, Valhalla).

collect()/export_outputs() — то же ядро для любого района: build_district.py
передаёт функции district_buildings/district_poi/district_linear(d_id) и свой
каталог. Запросы здесь не копируются.

Файлы генерируются, в git не коммитятся (см. .gitignore).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "data"
from db import DB_DSN

BUILDINGS_QUERY = """
SELECT b.osm_id,
       ST_AsGeoJSON(b.geometry),
       h.height,
       s.index,
       s.breakdown,
       COALESCE(
           NULLIF(b.addr_street, ''),
           -- Улица потеряна в OSM, но номер дома есть: берём улицу у
           -- соседнего дома с тем же номером в радиусе 30 м (Пресня,
           -- «улица Брянский Пост, 16»). Формально это вывод, а не факт,
           -- поэтому радиус маленький и номер должен совпадать.
           (
               SELECT n.addr_street
               FROM buildings n
               WHERE n.addr_street IS NOT NULL AND n.addr_street <> ''
                 AND n.addr_housenumber IS NOT NULL
                 AND b.addr_housenumber IS NOT NULL
                 AND split_part(n.addr_housenumber, ' ', 1) = split_part(b.addr_housenumber, ' ', 1)
                 AND ST_DWithin(n.geometry::geography, b.geometry::geography, 30)
               ORDER BY n.geometry <-> b.geometry
               LIMIT 1
           )
       ) AS addr_street,
       b.addr_housenumber,
       y.building_year,
       b.is_home
FROM {buildings} b
JOIN buildings_with_height h ON h.osm_id = b.osm_id
LEFT JOIN scores s ON s.building_id = b.osm_id
LEFT JOIN building_year_match y ON y.osm_id = b.osm_id
"""

POI_QUERY = """
SELECT type, subtype, name, ST_AsGeoJSON(geometry)
FROM {poi} p
WHERE type IN ('school', 'kindergarten', 'park', 'subway_entrance', 'rail_platform')
   OR (type = 'shop' AND subtype = ANY(%s))
"""

REDZONES_QUERY = """
SELECT kind, class, ST_AsGeoJSON(geometry)
FROM (
    SELECT kind, class, geometry FROM {linear} l
    WHERE (kind = 'road' AND class IN ('trunk', 'primary', 'secondary'))
       OR (kind = 'railway' AND class IN ('rail', 'narrow_gauge', 'light_rail'))
    UNION ALL
    SELECT z.kind, NULL AS class, z.geometry
    FROM zones z
    CROSS JOIN ({district_geom}) pr
    WHERE ST_Intersects(z.geometry, pr.geometry)
) t
"""

# Пешие маршруты (V2, Valhalla) — один файл на здание: при выборе дома
# фронт догружает только его маршруты (~3 КБ), а не все 16 530 сразу.
# Только жилые: нежилым балл/маршруты/изохроны не положены.
ROUTES_QUERY = """
SELECT r.building_id, r.category, r.minutes, r.distance_m, ST_AsGeoJSON(r.geometry)
FROM routes r
WHERE r.building_id IN (SELECT osm_id FROM {buildings})
  AND r.building_id IN (SELECT osm_id FROM buildings WHERE is_home)
"""

PRESNYA = {
    "buildings": "buildings_presnya",
    "buildings_params": (),
    "poi": "poi_presnya",
    "poi_params": (),
    "linear": "linear_presnya",
    "linear_params": (),
    "district_geom": "SELECT geometry FROM presnensky",
    "district_geom_params": (),
}


def write_geojson(path: Path, features: list[dict]) -> None:
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_kb = path.stat().st_size / 1024
    print(f"{path.name}: {len(features):,} объектов ({size_kb:.0f} КиБ)")


def collect(
    cur,
    shop_subtypes: list[str],
    *,
    buildings: str,
    buildings_params: tuple = (),
    poi: str,
    poi_params: tuple = (),
    linear: str,
    linear_params: tuple = (),
    district_geom: str,
    district_geom_params: tuple = (),
    include_routes: bool = True,
) -> dict:
    """Собрать фичи района из произвольного SQL-скоупа (вьюха или функция)."""
    cur.execute(BUILDINGS_QUERY.format(buildings=buildings), list(buildings_params))
    buildings_features = [
        {
            "type": "Feature",
            "properties": {
                "id": osm_id,
                "index": float(index) if (index is not None and is_home) else None,
                "height": float(height) if height is not None else None,
                "breakdown": breakdown if is_home else None,
                "address": ", ".join(p for p in (street, housenumber) if p) or None,
                "year": year,
                "is_home": bool(is_home),
            },
            "geometry": json.loads(geom_json),
        }
        for osm_id, geom_json, height, index, breakdown, street, housenumber, year, is_home
        in cur.fetchall()
    ]

    cur.execute(
        POI_QUERY.format(poi=poi),
        [*poi_params, shop_subtypes],
    )
    poi_features = [
        {
            "type": "Feature",
            "properties": {"type": t, "subtype": st, "name": name},
            "geometry": json.loads(geom_json),
        }
        for t, st, name, geom_json in cur.fetchall()
    ]

    cur.execute(
        REDZONES_QUERY.format(linear=linear, district_geom=district_geom),
        [*linear_params, *district_geom_params],
    )
    redzone_features = [
        {
            "type": "Feature",
            "properties": {"kind": kind, "class": cls},
            "geometry": json.loads(geom_json),
        }
        for kind, cls, geom_json in cur.fetchall()
    ]

    per_building: dict[int, dict] = {}
    if include_routes:
        cur.execute(ROUTES_QUERY.format(buildings=buildings), list(buildings_params))
        for building_id, category, minutes, distance_m, geom_json in cur.fetchall():
            entry = per_building.setdefault(building_id, {"categories": {}})
            entry["categories"][category] = {
                "minutes": float(minutes),
                "distance_m": distance_m,
                "geometry": json.loads(geom_json),
            }

    return {
        "buildings": buildings_features,
        "poi": poi_features,
        "redzones": redzone_features,
        "routes": per_building,
    }


def export_outputs(
    out_dir: Path,
    data: dict,
    *,
    buildings_name: str = "presnya_buildings.geojson",
    poi_name: str = "presnya_poi.geojson",
    redzones_name: str = "presnya_redzones.geojson",
    routes_dir: Path | None = None,
) -> None:
    """Записать geojson (и, если просили, routes/<id>.json) в out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_building = data.get("routes") or {}

    if routes_dir is not None and per_building:
        breakdown_by_id = {
            feature["properties"]["id"]: feature["properties"]["breakdown"]
            for feature in data["buildings"]
        }
        routes_dir.mkdir(parents=True, exist_ok=True)
        for building_id, entry in per_building.items():
            # breakdown нужен только для выбранного дома, поэтому он лежит
            # рядом с categories в routes/<id>.json, а не в тайлах (шаг B1).
            breakdown = breakdown_by_id.get(building_id)
            if breakdown is not None:
                entry["breakdown"] = breakdown
            (routes_dir / f"{building_id}.json").write_text(
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        print(f"routes/: {len(per_building):,} файлов маршрутов")

    write_geojson(out_dir / buildings_name, data["buildings"])
    write_geojson(out_dir / poi_name, data["poi"])
    write_geojson(out_dir / redzones_name, data["redzones"])


def main() -> int:
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    shop_subtypes = cfg["bonuses"]["categories"]["shops"].get("subtypes", [])

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        data = collect(cur, shop_subtypes, include_routes=True, **PRESNYA)

    export_outputs(OUT_DIR, data, routes_dir=OUT_DIR / "routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
