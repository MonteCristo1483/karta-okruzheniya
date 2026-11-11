#!/usr/bin/env python3
"""Извлекает границы районов (boundary=administrative, admin_level=8) из PBF.

Таблица districts (name, geometry EPSG:4326, PK — id relation OSM).
Источник границ для сверки T12 и будущих районов. Идемпотентна (upsert).
"""

from __future__ import annotations

import sys
import time

import osmium
import psycopg

PBF_PATH = "data/raw/moscow.osm.pbf"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
BATCH_SIZE = 50

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS districts (
    osm_id      BIGINT PRIMARY KEY,
    name        TEXT NOT NULL,
    geometry    GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS districts_geometry_idx ON districts USING GIST (geometry)
"""

UPSERT = """
INSERT INTO districts (osm_id, name, geometry)
VALUES (%s, %s, ST_GeomFromEWKB(decode(%s, 'hex')))
ON CONFLICT (osm_id) DO UPDATE
SET name = EXCLUDED.name, geometry = EXCLUDED.geometry
"""


def ring_coords(ring: osmium.osm.Ring) -> list[list[float]]:
    return [[n.lon, n.lat] for n in ring if n.location.valid()]


def main() -> int:
    started = time.monotonic()
    wkbfab = osmium.geom.WKBFactory()
    batch: list[tuple] = []
    count = 0
    skipped = 0

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_INDEX)

        for o in osmium.FileProcessor(PBF_PATH).with_areas():
            if not o.is_area() or o.from_way():
                continue
            if o.tags.get("boundary") != "administrative":
                continue
            if o.tags.get("admin_level") != "8":
                continue
            name = o.tags.get("name") or o.tags.get("name:ru")
            if not name:
                skipped += 1
                continue
            try:
                wkb_hex = wkbfab.create_multipolygon(o)
            except RuntimeError:
                skipped += 1
                continue
            batch.append((o.orig_id(), name, wkb_hex))
            count += 1
            if len(batch) >= BATCH_SIZE:
                cur.executemany(UPSERT, batch)
                batch.clear()
                print(f"\rРайонов: {count}", end="", flush=True)
        if batch:
            cur.executemany(UPSERT, batch)
        conn.commit()

    print(f"\nГотово: {count} районов, пропущено {skipped}, {time.monotonic() - started:.0f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
