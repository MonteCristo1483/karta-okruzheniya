#!/usr/bin/env python3
"""Импорт ручного списка промышленных источников (v2.industry).

Читает config/industry_sources.json: крупные объекты Москвы с координатами
и классом опасности по СанПиН 2.2.1/2.1.1.1200-03. Неизвестные объекты
(мелкие склады, гаражи, автосервисы во дворах) в список не входят — они
не штрафуют, а дают флаг industry:not_classified только если дом стоит
внутри полигона landuse=industrial.

Повторный запуск обновляет строки по id (idempotent upsert). Схема —
pipeline/migrations/006_industry_sources.sql + 007_industry_sources_status.sql.

  python pipeline/import_industry_sources.py
  python pipeline/import_industry_sources.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import DB_DSN  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "industry_sources.json"
MIGRATION = ROOT / "pipeline" / "migrations" / "006_industry_sources.sql"
MIGRATION_STATUS = ROOT / "pipeline" / "migrations" / "007_industry_sources_status.sql"

STATUSES = {"operating", "conservation", "outside_moscow", "unknown"}

UPSERT = """
INSERT INTO industry_sources
    (id, name, kind, hazard_class, lon, lat, source, status, geometry)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326))
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    kind = EXCLUDED.kind,
    hazard_class = EXCLUDED.hazard_class,
    lon = EXCLUDED.lon,
    lat = EXCLUDED.lat,
    source = EXCLUDED.source,
    status = EXCLUDED.status,
    geometry = EXCLUDED.geometry
"""


def load_sources(path: Path = CONFIG) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["sources"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт промышленных источников")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sources = load_sources()
    invalid = [s for s in sources if s["hazard_class"] not in {"I", "II", "III", "IV", "V"}]
    if invalid:
        print(f"Неверный класс у {len(invalid)} объектов", file=sys.stderr)
        return 1
    bad_status = [
        s for s in sources if s.get("status", "operating") not in STATUSES
    ]
    if bad_status:
        print(f"Неверный status у {len(bad_status)} объектов", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"БЫЛО БЫ: {len(sources)} источников")
        return 0

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
        cur.execute(MIGRATION_STATUS.read_text(encoding="utf-8"))
        cur.executemany(
            UPSERT,
            [
                (
                    s["id"], s["name"], s["kind"], s["hazard_class"],
                    s["lon"], s["lat"], s["source"],
                    s.get("status", "operating"), s["lon"], s["lat"],
                )
                for s in sources
            ],
        )
        conn.commit()

    by_class: dict[str, int] = {}
    for source in sources:
        by_class[source["hazard_class"]] = by_class.get(source["hazard_class"], 0) + 1
    by_status: dict[str, int] = {}
    for source in sources:
        status = source.get("status", "operating")
        by_status[status] = by_status.get(status, 0) + 1
    print(
        f"Источников: {len(sources)} "
        f"({', '.join(f'{cls} {count}' for cls, count in sorted(by_class.items()))}); "
        f"статусы: {', '.join(f'{st} {n}' for st, n in sorted(by_status.items()))}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
