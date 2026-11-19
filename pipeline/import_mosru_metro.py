#!/usr/bin/env python3
"""Импорт недостающих входов метро с data.mos.ru в poi.

Берём набор 624 (входы/выходы вестибюлей метро), фильтруем действующие
точки на территории Москвы и добавляем только те, у которых рядом нет
нашего POI `subway_entrance` (допуск 30 м). Так официальный источник
закрывает дыры OSM, но не задваивает маркеры на карте.

Идемпотентность: (osm_type='mosru', osm_id=global_id), повторный запуск
ничего не добавляет.

Требуется: PostGIS, ключ MOSRU_API_KEY в .env.

  python pipeline/import_mosru_metro.py --dry-run
  python pipeline/import_mosru_metro.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import features  # noqa: E402

DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
DATASET = 624
DEDUPE_M = 30.0

ALTER_TABLE = """
ALTER TABLE poi ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE poi ADD COLUMN IF NOT EXISTS address TEXT
"""

NEAREST = """
SELECT osm_id, ST_Distance(
           geometry::geography,
           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
       )
FROM poi
WHERE type = 'subway_entrance'
ORDER BY geometry <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
LIMIT 1
"""

INSERT = """
INSERT INTO poi (osm_type, osm_id, type, subtype, geometry, name, address)
VALUES ('mosru', %s, 'subway_entrance', 'mosru',
        ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
ON CONFLICT (osm_type, osm_id) DO NOTHING
"""


def is_active(attributes: dict) -> bool:
    status = str(attributes.get("ObjectStatus", "действует")).strip().lower()
    return status in ("действует", "действующий", "")


def is_in_moscow(attributes: dict) -> bool:
    value = attributes.get("OnTerritoryOfMoscow")
    if value is None:
        return True
    return str(value).strip().lower() in ("да", "true", "1", "yes")


def fetch_official() -> list[dict]:
    points: list[dict] = []
    skip = 0
    while True:
        page = features(DATASET, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if not is_in_moscow(attributes) or not is_active(attributes):
                continue
            coordinates = (feature.get("geometry") or {}).get("coordinates")
            if not coordinates:
                continue
            lon, lat = coordinates
            points.append({
                "global_id": int(attributes.get("global_id") or 0),
                "lon": float(lon),
                "lat": float(lat),
                "name": attributes.get("Name"),
                "station": attributes.get("NameOfStation"),
            })
        if len(page) < 1000:
            break
        skip += 1000
    return points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт входов метро с data.mos.ru")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что было бы добавлено")
    args = parser.parse_args(argv)

    points = fetch_official()
    stats = {"official": len(points), "inserted": 0, "already": 0, "no_id": 0}

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(ALTER_TABLE)
        for point in points:
            if point["global_id"] <= 0:
                stats["no_id"] += 1
                continue
            cur.execute(NEAREST, [point["lon"], point["lat"],
                                  point["lon"], point["lat"]])
            nearest = cur.fetchone()
            if nearest is not None and nearest[1] is not None and nearest[1] <= DEDUPE_M:
                stats["already"] += 1
                continue
            if args.dry_run:
                stats["inserted"] += 1
                continue
            cur.execute(INSERT, [
                point["global_id"], point["lon"], point["lat"],
                point["name"], point["station"],
            ])
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
