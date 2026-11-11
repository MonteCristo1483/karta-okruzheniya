#!/usr/bin/env python3
"""Сверка скоринга с эталонным набором (T12).

1. Парсит data/reference_set.md (район, группа, прогноз медианы).
2. Сопоставляет с districts (извлечены pipeline/extract_districts.py).
3. Считает индекс для всех зданий каждого района (scoring.run по скоупу).
4. Пишет data/reference_check.csv: район, группа, прогноз, медиана, p25, p75.

Прогноз считается попавшим, если медиана внутри [lo, hi].
"""

from __future__ import annotations

import csv
import re
import sys
from decimal import Decimal
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF_PATH = ROOT / "data" / "reference_set.md"
OUT_CSV = ROOT / "data" / "reference_check.csv"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

FORECAST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)")
SEPARATOR_CHARS = set("-–—: ")

SCOPE = """(
    SELECT b.* FROM buildings b
    JOIN districts d ON ST_Intersects(b.geometry, d.geometry)
    WHERE d.osm_id = %s
)"""

DISTRICT_STATS = """
SELECT d.osm_id, d.name,
       count(s.building_id) AS buildings,
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY s.index)::numeric, 1) AS p25,
       round(percentile_cont(0.5)  WITHIN GROUP (ORDER BY s.index)::numeric, 1) AS median,
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY s.index)::numeric, 1) AS p75
FROM districts d
JOIN buildings b ON ST_Intersects(b.geometry, d.geometry)
JOIN scores s ON s.building_id = b.osm_id
WHERE d.osm_id = ANY(%s)
GROUP BY d.osm_id, d.name
"""

AVG_COMPONENTS = """
SELECT d.name,
       round(avg((s.breakdown->'bonuses'->'transport'->>'bonus')::numeric), 2) AS transport,
       round(avg((s.breakdown->'bonuses'->'schools'->>'bonus')::numeric), 2) AS schools,
       round(avg((s.breakdown->'bonuses'->'parks'->>'bonus')::numeric), 2) AS parks,
       round(avg((s.breakdown->'bonuses'->'shops'->>'bonus')::numeric), 2) AS shops,
       round(avg((s.breakdown->'penalties'->'road'->>'penalty')::numeric), 2) AS road,
       round(avg((s.breakdown->'penalties'->'railway'->>'penalty')::numeric), 2) AS railway
FROM districts d
JOIN buildings b ON ST_Intersects(b.geometry, d.geometry)
JOIN scores s ON s.building_id = b.osm_id
WHERE d.osm_id = %s
GROUP BY d.osm_id, d.name
"""


def parse_reference() -> list[dict]:
    refs = []
    for line in REF_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0] == "Район" or set(cells[0]) <= SEPARATOR_CHARS:
            continue
        district, group, forecast = cells
        match = FORECAST_RE.search(forecast)
        if not match:
            print(f"WARNING: не разобрал прогноз '{forecast}' у {district}", file=sys.stderr)
            continue
        refs.append({
            "district": district,
            "group": group,
            "lo": Decimal(match.group(1)),
            "hi": Decimal(match.group(2)),
        })
    return refs


def normalize(name: str) -> str:
    return name.replace("ё", "е").lower().replace("район", "").strip()


def match_districts(cur: psycopg.Cursor, refs: list[dict]) -> list[dict]:
    cur.execute("SELECT osm_id, name FROM districts")
    districts = cur.fetchall()
    matched = []
    unmatched = []
    for ref in refs:
        key = normalize(ref["district"])
        candidates = [
            (osm_id, name) for osm_id, name in districts
            if key and (key in normalize(name) or normalize(name) in key)
        ]
        if not candidates:
            unmatched.append(ref["district"])
            continue
        candidates.sort(key=lambda x: (normalize(x[1]) != key, len(x[1])))
        ref["osm_id"], ref["osm_name"] = candidates[0]
        matched.append(ref)
    if unmatched:
        print(f"Не сопоставлено: {', '.join(unmatched)}", file=sys.stderr)
        print("Доступные в districts: " + ", ".join(sorted(n for _, n in districts)), file=sys.stderr)
    return matched


def main() -> int:
    refs = parse_reference()
    print(f"Эталонный набор: {len(refs)} районов")

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        matched = match_districts(cur, refs)
        if len(matched) != len(refs):
            print("Есть несопоставленные районы — CSV соберу по сопоставленным, сверь имена.", file=sys.stderr)

        cfg = scoring.load_config()
        for i, ref in enumerate(matched, 1):
            print(f"[{i}/{len(matched)}] {ref['district']} (OSM: {ref['osm_name']})")
            scoring.run(SCOPE, [ref["osm_id"]], cfg)

        ids = [ref["osm_id"] for ref in matched]
        cur.execute(DISTRICT_STATS, [ids])
        stats = {row[0]: row for row in cur.fetchall()}

        rows = []
        for ref in matched:
            stat = stats.get(ref["osm_id"])
            if stat is None:
                continue
            median = stat[4]
            if median < ref["lo"]:
                verdict = "ниже прогноза"
            elif median > ref["hi"]:
                verdict = "выше прогноза"
            else:
                verdict = "в прогнозе"
            rows.append({
                "district": ref["district"], "group": ref["group"],
                "forecast": f"{ref['lo']}-{ref['hi']}",
                "median": str(median), "p25": str(stat[3]), "p75": str(stat[5]),
                "buildings": str(stat[2]),
                "verdict": verdict, "osm_name": ref["osm_name"],
            })

        with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        print("\nСредние компоненты индекса по районам (для разбора):")
        for ref in matched:
            cur.execute(AVG_COMPONENTS, [ref["osm_id"]])
            row = cur.fetchone()
            print(
                f"{ref['district']:<22} транспорт {row[1]:>5}  школы {row[2]:>5}  "
                f"парки {row[3]:>5}  магазины {row[4]:>5}  дороги {row[5]:>6}  ж/д {row[6]:>6}"
            )

    print(f"\nCSV: {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
