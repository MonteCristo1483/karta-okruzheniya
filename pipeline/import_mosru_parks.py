#!/usr/bin/env python3
"""Импорт недостающих парков с data.mos.ru (набор 1465) в poi.

У реестра настоящие полигоны, поэтому берём центроид — это тот же способ,
которым OSM-площади превращаются в POI. Добавляем только те парки, у
которых в 100 м нет нашего POI type='park' (дедупликация и идемпотентность).

  python pipeline/import_mosru_parks.py --dry-run
  python pipeline/import_mosru_parks.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import features  # noqa: E402

DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
DATASET = 1465
DISTRICT = "Пресненский"
DEDUPE_M = 100.0

NEAREST = """
SELECT count(*)
FROM poi p
WHERE p.type = 'park'
  AND ST_DWithin(p.geometry::geography, ST_GeomFromGeoJSON(%s)::geography, %s)
"""

INSERT = """
INSERT INTO poi (osm_type, osm_id, type, subtype, geometry, name, address)
VALUES ('mosru', %s, 'park', 'mosru',
        ST_Centroid(ST_GeomFromGeoJSON(%s)), %s, NULL)
ON CONFLICT (osm_type, osm_id) DO NOTHING
"""


def official_parks() -> list[dict]:
    parks: list[dict] = []
    skip = 0
    while True:
        page = features(DATASET, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if DISTRICT not in str(attributes.get("District", "")):
                continue
            if (feature.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            parks.append({
                "id": attributes.get("ID"),
                "name": attributes.get("CommonName"),
                "geometry": feature["geometry"],
            })
        if len(page) < 1000:
            break
        skip += 1000
    return parks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт недостающих парков")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    parks = official_parks()
    stats = {"official": len(parks), "inserted": 0, "already": 0, "no_id": 0}

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        for park in parks:
            if not park["id"]:
                stats["no_id"] += 1
                continue
            geometry = json.dumps(park["geometry"])
            cur.execute(NEAREST, [geometry, DEDUPE_M])
            if cur.fetchone()[0]:
                stats["already"] += 1
                continue
            if args.dry_run:
                stats["inserted"] += 1
                continue
            cur.execute(INSERT, [park["id"], geometry, park["name"]])
            stats["inserted"] += cur.rowcount
        if not args.dry_run:
            conn.commit()

    prefix = "БЫЛО БЫ " if args.dry_run else ""
    print(
        f"{prefix}добавлено: {stats['inserted']}, уже было: {stats['already']}, "
        f"без id: {stats['no_id']}, всего официальных: {stats['official']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
