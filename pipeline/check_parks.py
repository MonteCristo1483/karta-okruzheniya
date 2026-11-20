#!/usr/bin/env python3
"""Сверка парков с официальным реестром (data.mos.ru 1465).

Официальный набор — полигоны парковых территорий. Наши парки — точки
(центроиды OSM-полигонов). Парк считаем покрытым, если внутри полигона
или в 100 м от него есть наш POI type='park'.

  python pipeline/check_parks.py --out docs/research/parks-recon.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import features  # noqa: E402

from db import DB_DSN
DATASET = 1465
DISTRICT = "Пресненский"
MATCH_M = 100.0


def official_parks() -> list[dict]:
    parks: list[dict] = []
    skip = 0
    while True:
        page = features(DATASET, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if DISTRICT not in str(attributes.get("District", "")):
                continue
            if (feature.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
                continue
            parks.append({
                "id": attributes.get("ID"),
                "name": attributes.get("CommonName"),
                "geometry": feature["geometry"],
            })
        if len(page) < 1000:
            break
        skip += 1000
    return parks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка парков с реестром")
    parser.add_argument("--out", help="markdown-отчёт")
    args = parser.parse_args(argv)

    parks = official_parks()
    covered, missing = [], []
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM poi_presnya WHERE type = 'park'")
        ours_total = cur.fetchone()[0]
        for park in parks:
            cur.execute(
                """
                SELECT count(*),
                       min(ST_Distance(p.geometry::geography,
                                       ST_GeomFromGeoJSON(%s)::geography))
                FROM poi p
                WHERE p.type = 'park'
                  AND ST_DWithin(p.geometry::geography,
                                 ST_GeomFromGeoJSON(%s)::geography, %s)
                """,
                [json.dumps(park["geometry"]), json.dumps(park["geometry"]), MATCH_M],
            )
            count, distance = cur.fetchone()
            if count:
                covered.append(park)
            else:
                missing.append({**park, "nearest_m": None if distance is None
                                else round(distance, 1)})

    lines = [
        "# Сверка парков с реестром (data.mos.ru 1465)",
        "",
        f"Официальных парковых территорий в Пресне: {len(parks)}",
        f"Наших парков (POI): {ours_total}",
        f"Покрыто (≤ {MATCH_M:.0f} м): {len(covered)}",
        f"Не покрыто: {len(missing)}",
        "",
    ]
    if missing:
        lines.append("Официальные парки без нашего POI:")
        for park in missing:
            distance = "нет точек" if park["nearest_m"] is None else f"{park['nearest_m']} м"
            lines.append(f"  - {park['name']} (id {park['id']}): {distance}")
        lines.append("")
    lines.append(
        "Примечание: у реестра настоящие полигоны, поэтому недостающие парки "
        "можно импортировать как центроиды (как уже делается для OSM-площадей) "
        "— это факт, а не допущение."
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
