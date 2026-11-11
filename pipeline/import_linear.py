#!/usr/bin/env python3
"""Импорт линейных объектов (дороги, ж/д) из data/raw/moscow.osm.pbf в PostGIS.

Таблица linear:
  kind = 'road'     — highway=trunk/primary/secondary (классы из SPEC §3,
                      прокси шума и выбросов; веса trunk×1.0, primary×0.7,
                      secondary×0.4 подставляются на этапе скоринга);
  kind = 'railway'  — любой railway=* (way).

Геометрия — линии в WGS84 (EPSG:4326).

Требования: запущенный PostGIS (docker compose up -d) и зависимости
из pipeline/requirements.txt в pipeline/.venv.
"""

from __future__ import annotations

import sys
import time

import osmium
import psycopg

PBF_PATH = "data/raw/moscow.osm.pbf"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
ROAD_CLASSES = ("trunk", "primary", "secondary")
BATCH_SIZE = 10_000
REPORT_EVERY = 50_000

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS linear (
    osm_type TEXT NOT NULL,
    osm_id   BIGINT NOT NULL,
    kind     TEXT NOT NULL,
    class    TEXT NOT NULL,
    geometry GEOMETRY(LINESTRING, 4326) NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS linear_geometry_idx ON linear USING GIST (geometry);
CREATE INDEX IF NOT EXISTS linear_kind_class_idx ON linear (kind, class)
"""

INSERT = """
INSERT INTO linear (osm_type, osm_id, kind, class, geometry)
VALUES (%s, %s, %s, %s, ST_GeomFromEWKB(decode(%s, 'hex')))
ON CONFLICT (osm_type, osm_id) DO NOTHING
"""


def classify(tags: osmium.osm.TagList) -> tuple[str, str] | None:
    highway = tags.get("highway")
    if highway in ROAD_CLASSES:
        return ("road", highway)
    railway = tags.get("railway")
    if railway is not None:
        return ("railway", railway)
    return None


class LinearImporter(osmium.SimpleHandler):
    def __init__(self, cursor: psycopg.Cursor) -> None:
        super().__init__()
        self.wkbfab = osmium.geom.WKBFactory()
        self.cursor = cursor
        self.buffer: list[tuple] = []
        self.count = 0
        self.skipped = 0

    def way(self, w: osmium.osm.Way) -> None:
        obj = classify(w.tags)
        if obj is None:
            return
        kind, cls = obj
        try:
            wkb_hex = self.wkbfab.create_linestring(w)
        except RuntimeError:
            self.skipped += 1
            return
        self.buffer.append(("way", w.id, kind, cls, wkb_hex))
        self.count += 1
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()
        if self.count % REPORT_EVERY == 0:
            print(
                f"\rЛинейных объектов: {self.count:,} (без геометрии пропущено: {self.skipped:,})",
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
        cur.execute(CREATE_INDEX)

        importer = LinearImporter(cur)
        importer.apply_file(PBF_PATH, locations=True)

        importer.flush()
        conn.commit()

    print(
        f"\nГотово: {importer.count:,} объектов, пропущено {importer.skipped:,}, "
        f"{time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
