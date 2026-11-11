#!/usr/bin/env python3
"""Извлекает границу района Пресненский из data/raw/moscow.osm.pbf.

Relation 1275551 (boundary=administrative, admin_level=8, Пресненский район).
Результат:
  - pipeline/presnensky.geojson (коммитится в git, ODbL © OpenStreetMap);
  - таблица presnensky в PostGIS (одна строка, геометрия EPSG:4326) —
    источник для вьюх *_presnya (миграция 003_presnya_views.sql).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import osmium
import psycopg

PBF_PATH = "data/raw/moscow.osm.pbf"
GEOJSON_PATH = Path(__file__).resolve().parent / "presnensky.geojson"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

RELATION_ID = 1275551

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS presnensky (
    id       SMALLINT PRIMARY KEY,
    name     TEXT NOT NULL,
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
)
"""

UPSERT = """
INSERT INTO presnensky (id, name, geometry)
VALUES (1, %s, ST_GeomFromEWKB(decode(%s, 'hex')))
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, geometry = EXCLUDED.geometry
"""


def ring_coords(ring: osmium.osm.Ring) -> list[list[float]]:
    return [[n.lon, n.lat] for n in ring if n.location.valid()]


def main() -> int:
    wkbfab = osmium.geom.WKBFactory()
    found: dict | None = None

    for o in osmium.FileProcessor(PBF_PATH).with_areas():
        if not o.is_area() or o.from_way():
            continue
        if o.orig_id() != RELATION_ID:
            continue
        if o.tags.get("boundary") != "administrative":
            print(f"WARNING: relation {RELATION_ID} без тега boundary=administrative", file=sys.stderr)
        multipolygon = []
        for outer in o.outer_rings():
            polygon = [ring_coords(outer)]
            for inner in o.inner_rings(outer):
                polygon.append(ring_coords(inner))
            multipolygon.append(polygon)
        found = {
            "name": o.tags.get("name") or o.tags.get("name:ru") or "Пресненский",
            "admin_level": o.tags.get("admin_level"),
            "multipolygon": multipolygon,
            "wkb_hex": wkbfab.create_multipolygon(o),
        }
        break

    if found is None:
        print(f"Relation {RELATION_ID} не найден в {PBF_PATH}", file=sys.stderr)
        return 1

    feature = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": found["name"],
                    "osm_relation_id": RELATION_ID,
                    "admin_level": found["admin_level"],
                    "source": "OpenStreetMap contributors, ODbL",
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": found["multipolygon"],
                },
            }
        ],
    }
    GEOJSON_PATH.write_text(json.dumps(feature, ensure_ascii=False), encoding="utf-8")
    size_kb = GEOJSON_PATH.stat().st_size / 1024

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(UPSERT, (found["name"], found["wkb_hex"]))
        conn.commit()

    print(
        f"Готово: {found['name']} (relation {RELATION_ID}, "
        f"{len(found['multipolygon'])} полигонов) -> {GEOJSON_PATH.name} ({size_kb:.0f} КиБ), "
        f"таблица presnensky обновлена"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
