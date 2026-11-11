#!/usr/bin/env python3
"""Импорт штрафных зон (промзоны, стройки) из data/raw/moscow.osm.pbf.

Таблица zones (GEOMETRY(MULTIPOLYGON, 4326), kind):
  industrial    — landuse=industrial (без commercial);
  construction  — landuse=construction и building=construction.

Идемпотентно (upsert по osm_type/osm_id), GiST-индекс по геометрии.
"""

from __future__ import annotations

import sys
import time

import osmium
import psycopg

PBF_PATH = "data/raw/moscow.osm.pbf"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
BATCH_SIZE = 2_000
REPORT_EVERY = 10_000

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS zones (
    osm_type TEXT NOT NULL,
    osm_id   BIGINT NOT NULL,
    kind     TEXT NOT NULL,
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS zones_geometry_idx ON zones USING GIST (geometry);
CREATE INDEX IF NOT EXISTS zones_kind_idx ON zones (kind)
"""

UPSERT = """
INSERT INTO zones (osm_type, osm_id, kind, geometry)
VALUES (%s, %s, %s, ST_GeomFromEWKB(decode(%s, 'hex')))
ON CONFLICT (osm_type, osm_id) DO UPDATE
SET kind = EXCLUDED.kind, geometry = EXCLUDED.geometry
"""


def classify(tags: osmium.osm.TagList) -> str | None:
    landuse = tags.get("landuse")
    if landuse == "industrial":
        return "industrial"
    if landuse == "construction" or tags.get("building") == "construction":
        return "construction"
    return None


class ZoneImporter(osmium.SimpleHandler):
    def __init__(self, cursor: psycopg.Cursor) -> None:
        super().__init__()
        self.wkbfab = osmium.geom.WKBFactory()
        self.cursor = cursor
        self.buffer: list[tuple] = []
        self.count = 0
        self.skipped = 0

    def area(self, a: osmium.osm.Area) -> None:
        kind = classify(a.tags)
        if kind is None:
            return
        try:
            wkb_hex = self.wkbfab.create_multipolygon(a)
        except RuntimeError:
            self.skipped += 1
            return
        self.buffer.append(("area", a.id, kind, wkb_hex))
        self.count += 1
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()
        if self.count % REPORT_EVERY == 0:
            print(
                f"\rЗон: {self.count:,} (без геометрии пропущено: {self.skipped:,})",
                end="",
                flush=True,
            )

    def flush(self) -> None:
        if not self.buffer:
            return
        self.cursor.executemany(UPSERT, self.buffer)
        self.buffer.clear()


def main() -> int:
    started = time.monotonic()
    print(f"Читаю {PBF_PATH} ...")
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_INDEX)

        importer = ZoneImporter(cur)
        importer.apply_file(PBF_PATH)

        importer.flush()
        conn.commit()

    print(
        f"\nГотово: {importer.count:,} зон, пропущено {importer.skipped:,}, "
        f"{time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
