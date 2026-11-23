#!/usr/bin/env python3
"""Извлекает границу города Москвы из data/raw/moscow.osm.pbf (V2-S3b).

Граница ищется по тегам, а не по id: relation ``boundary=administrative``,
``admin_level=4`` и имя «Москва» (``ref=RU-MOW`` — дополнительная проверка,
если тег есть). В региональном PBF таких relation 18 (области ЦФО); берём
только город.

Результат:
  * таблица ``moscow`` (одна строка, id=1, геометрия EPSG:4326);
  * вьюха ``moscow_districts`` — районы из ``districts``, чей центроид
    попадает внутрь границы города (ST_Contains). Это официальный список
    районов для прогона и для отчёта run_city.py --stage boundaries.
  * отдельный geojson не пишется: граница нужна только в БД (публикация
    городской подложки — шаг tiles, формат не меняется).

Идемпотентно: повторный запуск перезаписывает строку и вьюху.
"""

from __future__ import annotations

import sys
import time

import osmium
import psycopg

PBF_PATH = "data/raw/moscow.osm.pbf"
from db import DB_DSN

CITY_NAMES = {"москва", "moscow"}
CITY_REF = "RU-MOW"
ADMIN_LEVEL = "4"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS moscow (
    id          SMALLINT PRIMARY KEY,
    osm_id      BIGINT NOT NULL,
    name        TEXT NOT NULL,
    admin_level TEXT,
    geometry    GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS moscow_geometry_idx ON moscow USING GIST (geometry)
"""

# Район принадлежит городу, если его центроид внутри границы Москвы.
# Пересечения геометрий недостаточно: районы Подмосковья на границе
# задевали бы город краем.
#
# Исключение — районы с битой/многосвязной геометрией, у которых центроид
# попадает вне своего полигона (в OSM так у Кунцево и Восточного): для них
# берём перекрытие площади с городом ≥50%. Иначе из прогона молча выпадали
# два реальных района (проверка 2026-11-23: 130 вместо 132).
CREATE_VIEW = """
CREATE OR REPLACE VIEW moscow_districts AS
SELECT d.osm_id, d.name, d.geometry
FROM districts d
JOIN moscow m ON (
    ST_Contains(m.geometry, ST_Centroid(d.geometry))
    OR ST_Area(ST_Intersection(d.geometry, m.geometry)::geography)
       >= 0.5 * ST_Area(d.geometry::geography)
)
"""

UPSERT = """
INSERT INTO moscow (id, osm_id, name, admin_level, geometry)
VALUES (1, %s, %s, %s, ST_GeomFromEWKB(decode(%s, 'hex')))
ON CONFLICT (id) DO UPDATE
SET osm_id      = EXCLUDED.osm_id,
    name        = EXCLUDED.name,
    admin_level = EXCLUDED.admin_level,
    geometry    = EXCLUDED.geometry
"""


def matches_city(tags: osmium.osm.TagView) -> bool:
    if tags.get("boundary") != "administrative":
        return False
    if tags.get("admin_level") != ADMIN_LEVEL:
        return False
    name = (tags.get("name:ru") or tags.get("name") or "").strip().lower()
    if name not in CITY_NAMES:
        return False
    ref = tags.get("ref")
    return ref is None or ref == CITY_REF


def main() -> int:
    started = time.monotonic()
    wkbfab = osmium.geom.WKBFactory()
    found: dict | None = None

    for o in osmium.FileProcessor(PBF_PATH).with_areas():
        if not o.is_area() or o.from_way():
            continue
        if not matches_city(o.tags):
            continue
        try:
            wkb_hex = wkbfab.create_multipolygon(o)
        except RuntimeError as exc:
            print(f"WARNING: relation {o.orig_id()} без геометрии: {exc}", file=sys.stderr)
            continue
        found = {
            "osm_id": o.orig_id(),
            "name": o.tags.get("name:ru") or o.tags.get("name") or "Москва",
            "admin_level": o.tags.get("admin_level"),
            "wkb_hex": wkb_hex,
        }
        break

    if found is None:
        print(
            f"Граница Москвы (admin_level=4, name=Москва) не найдена в {PBF_PATH}",
            file=sys.stderr,
        )
        return 1

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_INDEX)
        cur.execute(
            UPSERT,
            (found["osm_id"], found["name"], found["admin_level"], found["wkb_hex"]),
        )
        cur.execute(CREATE_VIEW)
        cur.execute("SELECT count(*), coalesce(sum(ST_Area(geometry::geography)), 0) FROM moscow")
        rows, area_m2 = cur.fetchone()
        cur.execute("SELECT count(*) FROM moscow_districts")
        districts = cur.fetchone()[0]
        conn.commit()

    if rows != 1:
        print(f"ОШИБКА: в moscow {rows} строк вместо одной", file=sys.stderr)
        return 1
    print(
        f"Готово: {found['name']} (relation {found['osm_id']}, admin_level="
        f"{found['admin_level']}), площадь {area_m2 / 1e6:.0f} км², "
        f"районов в границе: {districts}, {time.monotonic() - started:.0f} с"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
