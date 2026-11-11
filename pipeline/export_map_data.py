#!/usr/bin/env python3
"""Экспорт зданий Пресненского для веб-карты (M1).

web/public/data/presnya_buildings.geojson: геометрия (buildings_presnya),
высота (buildings_with_height), index + разбивка (scores), адрес если есть.
Файл генерируется скриптом, в git не коммитится (см. .gitignore).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "web" / "public" / "data" / "presnya_buildings.geojson"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

QUERY = """
SELECT b.osm_id,
       ST_AsGeoJSON(b.geometry),
       h.height,
       s.index,
       s.breakdown,
       b.addr_street,
       b.addr_housenumber
FROM buildings_presnya b
JOIN buildings_with_height h ON h.osm_id = b.osm_id
JOIN scores s ON s.building_id = b.osm_id
"""


def main() -> int:
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(QUERY)
        rows = cur.fetchall()

    features = []
    for osm_id, geom_json, height, index, breakdown, street, housenumber in rows:
        address = ", ".join(part for part in (street, housenumber) if part) or None
        features.append({
            "type": "Feature",
            "properties": {
                "id": osm_id,
                "index": float(index) if index is not None else None,
                "height": float(height) if height is not None else None,
                "breakdown": breakdown,
                "address": address,
            },
            "geometry": json.loads(geom_json),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = OUT_PATH.stat().st_size / (1 << 20)
    print(f"{len(features):,} зданий -> {OUT_PATH.relative_to(ROOT)} ({size_mb:.1f} МиБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
