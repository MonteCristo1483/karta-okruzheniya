#!/usr/bin/env python3
"""Импорт слоёв, которых нет в poi: зелёные полигоны и поликлиники (v2).

Один проход по data/raw/moscow.osm.pbf:

  green_areas  — полигоны зелени (leisure=park/garden/nature_reserve/common,
                 landuse=grass/forest/meadow/recreation_ground/village_green,
                 natural=wood/scrub/grassland/heath). Площадь считается в
                 метрах: ST_Area(geometry::geography), не в градусах. Нужна
                 для фактора «зелень» (config v2.green).

  poi          — type='polyclinic' из amenity=clinic/doctors/hospital и
                 healthcare=polyclinic/clinic. import_poi.py их не берёт, а
                 фактору «доступность» нужна поликлиника (v2.access).

Повторный запуск обновляет строки (idempotent upsert). Схема green_areas —
pipeline/migrations/005_green_areas.sql.

  python pipeline/import_v2_layers.py
  python pipeline/import_v2_layers.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import osmium
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import DB_DSN  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# osmium на Windows не открывает абсолютный путь с не-ASCII символами,
# поэтому читаем относительно cwd (как import_poi.py).
PBF_PATH = "data/raw/moscow.osm.pbf"
MIGRATION = ROOT / "pipeline" / "migrations" / "005_green_areas.sql"
BATCH_SIZE = 5_000

GREEN_LEISURE = {"park", "garden", "nature_reserve", "common"}
GREEN_LANDUSE = {"grass", "forest", "meadow", "recreation_ground", "village_green"}
GREEN_NATURAL = {"wood", "scrub", "grassland", "heath"}
CLINIC_AMENITY = {"clinic", "doctors", "hospital"}
CLINIC_HEALTHCARE = {"polyclinic", "clinic"}

UPSERT_GREEN = """
INSERT INTO green_areas (osm_type, osm_id, kind, area_m2, geometry)
VALUES (%s, %s, %s,
        ST_Area(ST_GeomFromEWKB(decode(%s, 'hex'))::geography),
        ST_Multi(ST_GeomFromEWKB(decode(%s, 'hex'))))
ON CONFLICT (osm_type, osm_id) DO UPDATE
SET kind = EXCLUDED.kind,
    area_m2 = EXCLUDED.area_m2,
    geometry = EXCLUDED.geometry
"""

UPSERT_CLINIC = """
INSERT INTO poi (osm_type, osm_id, type, subtype, geometry)
VALUES (%s, %s, 'polyclinic', %s, ST_Centroid(ST_GeomFromEWKB(decode(%s, 'hex'))))
ON CONFLICT (osm_type, osm_id) DO UPDATE
SET type = EXCLUDED.type,
    subtype = EXCLUDED.subtype,
    geometry = EXCLUDED.geometry
"""

# Скоринг считается только по Москве: зелень за её границей не нужна и
# раздувает таблицу (pbf — федеральный округ). Удаление идемпотентно.
CLEANUP_GREEN = """
DELETE FROM green_areas g
WHERE NOT EXISTS (
    SELECT 1 FROM moscow m WHERE ST_Intersects(g.geometry, m.geometry)
)
"""


def green_kind(tags: osmium.osm.TagList) -> str | None:
    leisure = tags.get("leisure")
    if leisure in GREEN_LEISURE:
        return leisure
    landuse = tags.get("landuse")
    if landuse in GREEN_LANDUSE:
        return landuse
    natural = tags.get("natural")
    if natural in GREEN_NATURAL:
        return natural
    return None


def clinic_subtype(tags: osmium.osm.TagList) -> str | None:
    if tags.get("amenity") in CLINIC_AMENITY:
        return tags.get("healthcare") or tags.get("amenity")
    if tags.get("healthcare") in CLINIC_HEALTHCARE:
        return tags.get("healthcare")
    return None


class V2LayerImporter(osmium.SimpleHandler):
    def __init__(self, cursor: psycopg.Cursor, dry_run: bool = False) -> None:
        super().__init__()
        self.wkbfab = osmium.geom.WKBFactory()
        self.cursor = cursor
        self.dry_run = dry_run
        self.green_buffer: list[tuple] = []
        self.clinic_buffer: list[tuple] = []
        self.stats = {"green": 0, "clinics": 0, "skipped": 0}

    def node(self, n: osmium.osm.Node) -> None:
        subtype = clinic_subtype(n.tags)
        if subtype is None:
            return
        try:
            wkb_hex = self.wkbfab.create_point(n)
        except RuntimeError:
            self.stats["skipped"] += 1
            return
        self.clinic_buffer.append(("node", n.id, subtype, wkb_hex))
        self.stats["clinics"] += 1
        if len(self.clinic_buffer) >= BATCH_SIZE:
            self.flush_clinics()

    def area(self, a: osmium.osm.Area) -> None:
        subtype = clinic_subtype(a.tags)
        if subtype is not None:
            try:
                wkb_hex = self.wkbfab.create_multipolygon(a)
            except RuntimeError:
                self.stats["skipped"] += 1
            else:
                self.clinic_buffer.append(("area", a.id, subtype, wkb_hex))
                self.stats["clinics"] += 1
        kind = green_kind(a.tags)
        if kind is not None:
            try:
                wkb_hex = self.wkbfab.create_multipolygon(a)
            except RuntimeError:
                self.stats["skipped"] += 1
            else:
                self.green_buffer.append(("area", a.id, kind, wkb_hex, wkb_hex))
                self.stats["green"] += 1
        if len(self.green_buffer) >= BATCH_SIZE:
            self.flush_green()
        if len(self.clinic_buffer) >= BATCH_SIZE:
            self.flush_clinics()

    def flush_green(self) -> None:
        if self.green_buffer and not self.dry_run:
            self.cursor.executemany(UPSERT_GREEN, self.green_buffer)
        self.green_buffer.clear()

    def flush_clinics(self) -> None:
        if self.clinic_buffer and not self.dry_run:
            self.cursor.executemany(UPSERT_CLINIC, self.clinic_buffer)
        self.clinic_buffer.clear()

    def flush(self) -> None:
        self.flush_green()
        self.flush_clinics()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт зелени и поликлиник v2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    started = time.monotonic()
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        if not args.dry_run:
            cur.execute(MIGRATION.read_text(encoding="utf-8"))
        importer = V2LayerImporter(cur, dry_run=args.dry_run)
        importer.apply_file(PBF_PATH)
        importer.flush()
        if not args.dry_run:
            cur.execute(CLEANUP_GREEN)
            conn.commit()

    prefix = "БЫЛО БЫ " if args.dry_run else ""
    print(
        f"{prefix}зелёных полигонов: {importer.stats['green']:,}, "
        f"поликлиник: {importer.stats['clinics']:,}, "
        f"без геометрии: {importer.stats['skipped']:,}, "
        f"{time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
