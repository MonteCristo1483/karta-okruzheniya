#!/usr/bin/env python3
"""HOMES-V2: быстрый refresh районов из PostGIS без пересборки POI/redzones.

Обновляет в `data/districts/<id>/buildings.geojson` только свойства
(`is_home`, `index`, `breakdown`, `address`, `year`), пересобирает
`buildings-index.json` и `summary.json`. Геометрия, POI и красные зоны
не трогаются — их пересборка идёт через build_district.py (часы), а здесь
нужны минуты после изменения классификации домов и скоринга.

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/refresh_districts.py
  pipeline/.venv/Scripts/python.exe pipeline/refresh_districts.py --districts 1275551,1257
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import DB_DSN  # noqa: E402
from build_tiles import build_index  # noqa: E402

DISTRICTS = ROOT / "data" / "districts"

PROPS_QUERY = """
SELECT b.osm_id,
       b.is_home,
       s.index,
       s.breakdown,
       coalesce(b.addr_street, ''),
       coalesce(b.addr_housenumber, ''),
       y.building_year
FROM buildings b
LEFT JOIN scores s ON s.building_id = b.osm_id
LEFT JOIN building_year_match y ON y.osm_id = b.osm_id
WHERE b.osm_id = ANY(%s)
"""


def district_dirs() -> list[Path]:
    dirs = []
    for path in DISTRICTS.iterdir():
        if (
            path.is_dir()
            and path.name.isdigit()
            and (path / "buildings.geojson").exists()
            and (path / "summary.json").exists()
        ):
            dirs.append(path)
    return sorted(dirs, key=lambda path: int(path.name))


def refresh(cur, path: Path) -> dict:
    started = time.perf_counter()
    geojson_path = path / "buildings.geojson"
    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = payload["features"]
    ids = [int(feature["properties"]["id"]) for feature in features]

    cur.execute(PROPS_QUERY, (ids,))
    rows = {
        int(osm_id): (is_home, index, breakdown, street, house, year)
        for osm_id, is_home, index, breakdown, street, house, year in cur.fetchall()
    }

    missing = 0
    homes_missing_index = 0
    for feature in features:
        props = feature["properties"]
        row = rows.get(int(props["id"]))
        if row is None:
            missing += 1
            continue
        is_home, index, breakdown, street, house, year = row
        props["is_home"] = bool(is_home)
        props["index"] = float(index) if (index is not None and is_home) else None
        props["breakdown"] = breakdown if is_home else None
        props["address"] = ", ".join(part for part in (street, house) if part) or None
        props["year"] = year
        if props["is_home"] and props["index"] is None:
            homes_missing_index += 1

    geojson_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    home_features = [
        feature
        for feature in features
        if feature["properties"]["is_home"] and feature["properties"]["index"] is not None
    ]
    index_rows = build_index(home_features)
    index_path = path / "buildings-index.json"
    index_path.write_text(
        json.dumps(index_rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    summary_path = path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["buildings"] = len(features)
    summary["homes"] = len(home_features)
    summary.setdefault("geojson_bytes", {})["buildings"] = geojson_path.stat().st_size
    summary["index_rows"] = len(index_rows)
    summary["index_bytes"] = index_path.stat().st_size
    summary["seconds"] = round(time.perf_counter() - started, 1)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "buildings": len(features),
        "homes": len(home_features),
        "missing": missing,
        "homes_missing_index": homes_missing_index,
    }


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--districts", default="all", help="all или osm_id через запятую")
    args = parser.parse_args(argv)

    wanted = None if args.districts == "all" else {
        int(value) for value in args.districts.split(",") if value.strip()
    }
    dirs = [
        path for path in district_dirs() if wanted is None or int(path.name) in wanted
    ]
    if not dirs:
        raise SystemExit("Нет районов data/districts/<id>/buildings.geojson")

    started = time.perf_counter()
    total_buildings = total_homes = total_missing = total_without_index = 0
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        for number, path in enumerate(dirs, 1):
            report = refresh(cur, path)
            total_buildings += report["buildings"]
            total_homes += report["homes"]
            total_missing += report["missing"]
            total_without_index += report["homes_missing_index"]
            print(
                f"{number:>3}/{len(dirs)} {path.name}: зданий {report['buildings']:,}, "
                f"жилых {report['homes']:,}, без балла {report['homes_missing_index']}, "
                f"{time.perf_counter() - started:.0f} с"
            )
        conn.commit()

    print(
        f"Готово: районов {len(dirs)}, зданий {total_buildings:,}, "
        f"жилых {total_homes:,}, нет в БД {total_missing}, "
        f"жилых без балла {total_without_index}, "
        f"{time.perf_counter() - started:.0f} с"
    )
    return 0 if total_missing == 0 and total_without_index == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
