#!/usr/bin/env python3
"""Импорт зданий из data/raw/moscow.osm.pbf в PostGIS (таблица buildings).

Читает PBF через osmium: area callback получает закрытые way и
multipolygon-отношения с тегом building=*. Геометрия в WGS84 (EPSG:4326)
загружается как WKB. Теги height, building:levels, addr:street,
addr:housenumber сохраняются текстом (сырые значения OSM, парсинг — в T4).
Весь набор тегов area пишется в tags JSONB, а is_home вычисляется по
config/buildings.json через buildings_filter.is_home (единственное правило).
Повторный импорт обновляет теги и флаг у существующих строк.

Требования: запущенный PostGIS (docker compose up -d) и установленные
зависимости из pipeline/requirements.txt в pipeline/.venv.
"""

from __future__ import annotations

import sys
import time

import osmium
import psycopg
from psycopg.types.json import Jsonb

from buildings_filter import is_home

PBF_PATH = "data/raw/moscow.osm.pbf"
from db import DB_DSN
BATCH_SIZE = 10_000
REPORT_EVERY = 50_000

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS buildings (
    osm_id           BIGINT PRIMARY KEY,
    geometry         GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    height           TEXT,
    building_levels  TEXT,
    addr_street      TEXT,
    addr_housenumber TEXT,
    tags             JSONB,
    is_home          BOOLEAN NOT NULL DEFAULT TRUE
)
"""

# База могла быть создана до появления tags/is_home: обновляем схему
# идемпотентно. Новые колонки добавляет и migration 004.
ALTER_TABLE = """
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS is_home BOOLEAN NOT NULL DEFAULT TRUE
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS buildings_geometry_idx ON buildings USING GIST (geometry)
"""

INSERT = """
INSERT INTO buildings (
    osm_id, geometry, height, building_levels, addr_street, addr_housenumber,
    tags, is_home
)
VALUES (%s, ST_GeomFromEWKB(decode(%s, 'hex')), %s, %s, %s, %s, %s, %s)
ON CONFLICT (osm_id) DO UPDATE
SET geometry         = EXCLUDED.geometry,
    height           = EXCLUDED.height,
    building_levels  = EXCLUDED.building_levels,
    addr_street      = EXCLUDED.addr_street,
    addr_housenumber = EXCLUDED.addr_housenumber,
    tags             = EXCLUDED.tags,
    is_home          = EXCLUDED.is_home
"""


class BuildingImporter(osmium.SimpleHandler):
    def __init__(self, cursor: psycopg.Cursor) -> None:
        super().__init__()
        self.wkbfab = osmium.geom.WKBFactory()
        self.cursor = cursor
        self.buffer: list[tuple] = []
        self.count = 0
        self.skipped = 0

    def area(self, a: osmium.osm.Area) -> None:
        if a.tags.get("building") is None:
            return
        try:
            wkb_hex = self.wkbfab.create_multipolygon(a)
        except RuntimeError:
            self.skipped += 1
            return
        tags = dict(a.tags)
        self.buffer.append((
            a.id,
            wkb_hex,
            tags.get("height"),
            tags.get("building:levels"),
            tags.get("addr:street"),
            tags.get("addr:housenumber"),
            Jsonb(tags),
            is_home(tags, a.id),
        ))
        self.count += 1
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()
        if self.count % REPORT_EVERY == 0:
            print(
                f"\rЗданий: {self.count:,} (без геометрии пропущено: {self.skipped:,})",
                end="",
                flush=True,
            )

    def flush(self) -> None:
        if not self.buffer:
            return
        self.cursor.executemany(INSERT, self.buffer)
        self.buffer.clear()


def main() -> int:
    started = time.monotonic()
    print(f"Читаю {PBF_PATH} ...")
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(ALTER_TABLE)
        cur.execute(CREATE_INDEX)

        importer = BuildingImporter(cur)
        importer.apply_file(PBF_PATH)

        importer.flush()
        conn.commit()

    print(
        f"\nГотово: {importer.count:,} зданий, пропущено {importer.skipped:,}, "
        f"{time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
