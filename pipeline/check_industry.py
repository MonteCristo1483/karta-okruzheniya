#!/usr/bin/env python3
"""Проверка фактора «промышленный источник» (v2.industry).

Два режима:

  python pipeline/check_industry.py --top 20
      топ жилых домов Москвы по штрафу за промышленные источники: имя
      объекта, класс, расстояние, value. Глазами: должны быть ТЭЦ/МНПЗ/МСЗ,
      а не случайные сараи.

  python pipeline/check_industry.py --point 55.7560,37.5545
      санити-чек точки: ближайшие источники, попадание внутрь полигона
      landuse=industrial и итоговый value. Для Мантулинской ожидается 0,
      даже если рядом гараж/склад/автосервис: они не классифицированы.

Ничего не пишет в БД.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring
import scoring_v2
from db import DB_DSN

MESSAGE = """Ближайшие промышленные источники (радиус по классу):
{rows}
Внутри полигона landuse=industrial: {inside}
Итог: value={value} status={status} class={hazard} distance={distance} м
"""


def show_point(cur, lat: float, lon: float, cfg: dict) -> int:
    cur.execute(
        """
        SELECT id, name, hazard_class, status,
               ST_Distance(geometry::geography,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
        FROM industry_sources
        ORDER BY geometry <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        LIMIT 5
        """,
        [lon, lat, lon, lat],
    )
    sources = [
        {"id": row[0], "name": row[1], "hazard_class": row[2],
         "status": row[3], "distance_m": float(row[4])}
        for row in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM zones z
            WHERE z.kind = 'industrial'
              AND ST_Intersects(z.geometry,
                                ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        )
        """,
        [lon, lat],
    )
    inside = bool(cur.fetchone()[0])
    entry = scoring_v2.industry_pressure(sources, cfg, inside)
    rows = "\n".join(
        f"  {source['name']} ({source['hazard_class']}, {source['status']}): "
        f"{source['distance_m']:.0f} м"
        for source in sources[:3]
    )
    print(MESSAGE.format(
        rows=rows or "  нет",
        inside="да" if inside else "нет",
        value=entry["value"],
        status=entry["status"],
        hazard=entry.get("hazard_class"),
        distance=entry.get("distance_m"),
    ))
    return 0


def show_top(cur, limit: int, cfg: dict) -> int:
    scope = (
        "(SELECT b.* FROM buildings b "
        "JOIN moscow m ON ST_Intersects(b.geometry, m.geometry) "
        "JOIN scores s ON s.building_id = b.osm_id WHERE b.is_home)"
    )
    industry = scoring.collect_v2_industry(cur, scope, [], cfg)
    ids = list(industry)
    cur.execute(
        "SELECT osm_id, addr_street, addr_housenumber, "
        "ST_Y(ST_Centroid(geometry)), ST_X(ST_Centroid(geometry)) "
        "FROM buildings WHERE osm_id = ANY(%s)",
        [ids],
    )
    info = {row[0]: row[1:] for row in cur.fetchall()}
    ranked = sorted(
        industry.items(), key=lambda item: item[1]["value"], reverse=True
    )[:limit]
    for osm_id, entry in ranked:
        street, house, lat, lon = info.get(osm_id, (None, None, None, None))
        address = " ".join(part for part in (street, house) if part) or "—"
        print(
            f"{entry['value']:.3f}  {entry['hazard_class']}  "
            f"{entry['distance_m']:5.0f} м  {entry['name']}  "
            f"({address}; {lat:.5f},{lon:.5f})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка v2.industry")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--point", help="lat,lon")
    args = parser.parse_args(argv)

    cfg = scoring.load_config()
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        if args.point:
            lat, lon = (float(part) for part in args.point.split(","))
            return show_point(cur, lat, lon, cfg)
        return show_top(cur, args.top, cfg)


if __name__ == "__main__":
    sys.exit(main())
