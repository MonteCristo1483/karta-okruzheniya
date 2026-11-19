#!/usr/bin/env python3
"""Сверка слоя стройки с реестром data.mos.ru (набор 2941).

Официальный набор — точки объектов капитального строительства со статусом
«в строительстве». Наши зоны стройки — полигоны OSM (landuse=construction,
building=construction). Проверяем, покрывает ли наш слой официальные
объекты: точка считается покрытой, если ближайший наш полигон в пределах
100 м (обычно точка лежит внутри полигона → 0 м).

Импорт полигонов из реестра не делаем: там точки, а штраф считается по
площади и расстоянию до контура. Сначала диагностика.

  python pipeline/check_construction.py --out docs/research/construction-recon.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import features  # noqa: E402

DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
DATASET = 2941
DISTRICT = "Пресненский"
STATUS = "в строительстве"
MATCH_M = 100.0

NEAREST_ZONE = """
SELECT z.osm_id, z.osm_type,
       ST_Distance(z.geometry::geography,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
FROM zones z
WHERE z.kind = 'construction'
  AND ST_Intersects(z.geometry, (SELECT geometry FROM presnensky))
ORDER BY z.geometry <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
LIMIT 1
"""

ZONE_COUNT = """
SELECT count(*) FROM zones z
WHERE z.kind = 'construction'
  AND ST_Intersects(z.geometry, (SELECT geometry FROM presnensky))
"""


def official_objects() -> list[dict]:
    items: list[dict] = []
    skip = 0
    while True:
        page = features(DATASET, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if DISTRICT not in str(attributes.get("District", "")):
                continue
            if STATUS not in str(attributes.get("ObjectStatus", "")):
                continue
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if not coordinates:
                continue
            point = coordinates[0] if isinstance(coordinates[0], (list, tuple)) else coordinates
            lon, lat = float(point[0]), float(point[1])
            items.append({
                "id": attributes.get("ID"),
                "name": attributes.get("ObjectName"),
                "address": attributes.get("ObjectAddress"),
                "lat": lat,
                "lon": lon,
            })
        if len(page) < 1000:
            break
        skip += 1000
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка стройки с реестром")
    parser.add_argument("--out", help="markdown-отчёт")
    args = parser.parse_args(argv)

    objects = official_objects()
    covered, missing = [], []
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(ZONE_COUNT)
        zones_total = cur.fetchone()[0]
        for item in objects:
            cur.execute(NEAREST_ZONE, [item["lon"], item["lat"],
                                       item["lon"], item["lat"]])
            row = cur.fetchone()
            distance = row[2] if row else None
            if distance is not None and distance <= MATCH_M:
                covered.append({**item, "zone_id": row[0], "distance_m": round(distance, 1)})
            else:
                missing.append({**item, "distance_m": None if distance is None else round(distance, 1)})

    lines = [
        "# Сверка стройки с реестром (data.mos.ru 2941)",
        "",
        f"В Пресне официальных объектов «{STATUS}»: {len(objects)}",
        f"Наших полигонов стройки в Пресне: {zones_total}",
        f"Покрыто (≤ {MATCH_M:.0f} м): {len(covered)}",
        f"Не покрыто: {len(missing)}",
        "",
    ]
    if missing:
        lines.append("Официальные объекты без нашей зоны:")
        for item in missing:
            near = "нет зоны" if item["distance_m"] is None else f"ближайшая в {item['distance_m']} м"
            lines.append(f"  - {item['name']} ({item['address']}): {near}")
        lines.append("")
    lines.append(
        "Примечание: реестр покрывает капитальные объекты; частные и мелкие "
        "стройки в OSM могут быть законно «лишними». Обратное — пропуск "
        "официального объекта — сигнал, что слой стройки неполный."
    )

    report = "\n".join(lines).strip()
    print(report)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report + "\n", encoding="utf-8")
        print(f"\nОтчёт записан: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
