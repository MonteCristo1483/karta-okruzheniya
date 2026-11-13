#!/usr/bin/env python3
"""Экспорт данных Пресненского для веб-карты (M1, M4).

web/public/data/:
  presnya_buildings.geojson — здания: геометрия, высота, index + разбивка, адрес;
  presnya_poi.geojson       — POI (type, subtype, name), магазины — только
                              subtype из config/scoring.json (как в скоринге);
  presnya_redzones.geojson  — «красные зоны»: линии магистралей и ж/д
                              (linear) + полигоны строек и промзон (zones).

Файлы генерируются, в git не коммитятся (см. .gitignore).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "data"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

BUILDINGS_QUERY = """
SELECT b.osm_id,
       ST_AsGeoJSON(b.geometry),
       h.height,
       s.index,
       s.breakdown,
       b.addr_street,
       b.addr_housenumber,
       y.building_year
FROM buildings_presnya b
JOIN buildings_with_height h ON h.osm_id = b.osm_id
JOIN scores s ON s.building_id = b.osm_id
LEFT JOIN building_year_match y ON y.osm_id = b.osm_id
"""

POI_QUERY = """
SELECT type, subtype, name, ST_AsGeoJSON(geometry)
FROM poi_presnya
WHERE type IN ('school', 'kindergarten', 'park', 'subway_entrance')
   OR (type = 'shop' AND subtype = ANY(%s))
"""

REDZONES_QUERY = """
SELECT kind, class, ST_AsGeoJSON(geometry)
FROM (
    SELECT kind, class, geometry FROM linear_presnya
    WHERE (kind = 'road' AND class IN ('trunk', 'primary', 'secondary'))
       OR (kind = 'railway' AND class IN ('rail', 'narrow_gauge', 'light_rail'))
    UNION ALL
    SELECT z.kind, NULL AS class, z.geometry
    FROM zones z
    CROSS JOIN presnensky pr
    WHERE ST_Intersects(z.geometry, pr.geometry)
) t
"""

# Пешие маршруты (V2, Valhalla) — один файл на здание: при выборе дома
# фронт догружает только его маршруты (~3 КБ), а не все 16 530 сразу.
ROUTES_QUERY = """
SELECT building_id, category, minutes, distance_m, ST_AsGeoJSON(geometry)
FROM routes
WHERE building_id IN (SELECT osm_id FROM buildings_presnya)
"""


def write_geojson(path: Path, features: list[dict]) -> None:
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_kb = path.stat().st_size / 1024
    print(f"{path.name}: {len(features):,} объектов ({size_kb:.0f} КиБ)")


def main() -> int:
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    shop_subtypes = cfg["bonuses"]["categories"]["shops"].get("subtypes", [])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(BUILDINGS_QUERY)
        buildings = [
            {
                "type": "Feature",
                "properties": {
                    "id": osm_id,
                    "index": float(index),
                    "height": float(height) if height is not None else None,
                    "breakdown": breakdown,
                    "address": ", ".join(p for p in (street, housenumber) if p) or None,
                    "year": year,
                },
                "geometry": json.loads(geom_json),
            }
            for osm_id, geom_json, height, index, breakdown, street, housenumber, year in cur.fetchall()
        ]

        cur.execute(POI_QUERY, [shop_subtypes])
        poi = [
            {
                "type": "Feature",
                "properties": {"type": t, "subtype": st, "name": name},
                "geometry": json.loads(geom_json),
            }
            for t, st, name, geom_json in cur.fetchall()
        ]

        cur.execute(REDZONES_QUERY)
        redzones = [
            {
                "type": "Feature",
                "properties": {"kind": kind, "class": cls},
                "geometry": json.loads(geom_json),
            }
            for kind, cls, geom_json in cur.fetchall()
        ]

        cur.execute(ROUTES_QUERY)
        per_building: dict[int, dict] = {}
        for building_id, category, minutes, distance_m, geom_json in cur.fetchall():
            entry = per_building.setdefault(building_id, {"categories": {}})
            entry["categories"][category] = {
                "minutes": float(minutes),
                "distance_m": distance_m,
                "geometry": json.loads(geom_json),
            }

    routes_dir = OUT_DIR / "routes"
    routes_dir.mkdir(parents=True, exist_ok=True)
    for building_id, entry in per_building.items():
        (routes_dir / f"{building_id}.json").write_text(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    write_geojson(OUT_DIR / "presnya_buildings.geojson", buildings)
    write_geojson(OUT_DIR / "presnya_poi.geojson", poi)
    write_geojson(OUT_DIR / "presnya_redzones.geojson", redzones)
    print(f"routes/: {len(per_building):,} файлов маршрутов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
