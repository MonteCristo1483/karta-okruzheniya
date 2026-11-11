#!/usr/bin/env python3
"""Импорт POI из data/raw/moscow.osm.pbf в PostGIS (таблица poi).

Категории (колонка type), по SPEC §5:
  school           — amenity=school
  kindergarten     — amenity=kindergarten
  park             — leisure=park
  subway_entrance  — railway=subway_entrance
  shop             — любой тег shop=*

Точки берутся из node; площадные объекты (закрытые way и multipolygon-
отношения) приходят через area callback и сводятся к центроиду в SQL.
Геометрия в WGS84 (EPSG:4326).

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
BATCH_SIZE = 10_000
REPORT_EVERY = 50_000

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS poi (
    osm_type TEXT NOT NULL,
    osm_id   BIGINT NOT NULL,
    type     TEXT NOT NULL,
    geometry GEOMETRY(POINT, 4326) NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS poi_geometry_idx ON poi USING GIST (geometry);
CREATE INDEX IF NOT EXISTS poi_type_idx ON poi (type)
"""

INSERT = """
INSERT INTO poi (osm_type, osm_id, type, geometry)
VALUES (%s, %s, %s, ST_Centroid(ST_GeomFromEWKB(decode(%s, 'hex'))))
ON CONFLICT (osm_type, osm_id) DO NOTHING
"""


def classify(tags: osmium.osm.TagList) -> str | None:
    if tags.get("railway") == "subway_entrance":
        return "subway_entrance"
    if tags.get("amenity") == "school":
        return "school"
    if tags.get("amenity") == "kindergarten":
        return "kindergarten"
    if tags.get("leisure") == "park":
        return "park"
    if tags.get("shop") is not None:
        return "shop"
    return None


class PoiImporter(osmium.SimpleHandler):
    def __init__(self, cursor: psycopg.Cursor) -> None:
        super().__init__()
        self.wkbfab = osmium.geom.WKBFactory()
        self.cursor = cursor
        self.buffer: list[tuple] = []
        self.count = 0
        self.skipped = 0

    def node(self, n: osmium.osm.Node) -> None:
        poi_type = classify(n.tags)
        if poi_type is None:
            return
        try:
            wkb_hex = self.wkbfab.create_point(n)
        except RuntimeError:
            self.skipped += 1
            return
        self.add("node", n.id, poi_type, wkb_hex)

    def area(self, a: osmium.osm.Area) -> None:
        poi_type = classify(a.tags)
        if poi_type is None:
            return
        try:
            wkb_hex = self.wkbfab.create_multipolygon(a)
        except RuntimeError:
            self.skipped += 1
            return
        self.add("area", a.id, poi_type, wkb_hex)

    def add(self, osm_type: str, osm_id: int, poi_type: str, wkb_hex: str) -> None:
        self.buffer.append((osm_type, osm_id, poi_type, wkb_hex))
        self.count += 1
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()
        if self.count % REPORT_EVERY == 0:
            print(
                f"\rPOI: {self.count:,} (без геометрии пропущено: {self.skipped:,})",
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

        importer = PoiImporter(cur)
        importer.apply_file(PBF_PATH)

        importer.flush()
        conn.commit()

    print(
        f"\nГотово: {importer.count:,} POI, пропущено {importer.skipped:,}, "
        f"{time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
