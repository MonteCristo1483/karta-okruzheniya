#!/usr/bin/env python3
"""Разовый офлайн-мерж: breakdown из geojson в routes/<id>.json (шаг B1).

`export_map_data.py` пишет breakdown в routes-файлы сам, но текущая
выгрузка сделана до этого изменения. Скрипт дописывает поле без PostGIS:

    python pipeline/add_breakdown_to_routes.py --data-dir web/public/data

Идемпотентно: если breakdown уже совпадает с geojson, файл не перезаписывается.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "web" / "public" / "data"


def load_breakdowns(geojson_path: Path) -> dict[int, dict]:
    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    result: dict[int, dict] = {}
    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        building_id = props.get("id")
        if building_id is None:
            continue
        result[building_id] = props.get("breakdown")
    return result


def dump_routes(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="каталог выгрузки (по умолчанию web/public/data)",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    geojson_path = data_dir / "presnya_buildings.geojson"
    routes_dir = data_dir / "routes"
    if not geojson_path.is_file():
        print(f"нет файла {geojson_path}", file=sys.stderr)
        return 2
    if not routes_dir.is_dir():
        print(f"нет каталога {routes_dir}", file=sys.stderr)
        return 2

    breakdowns = load_breakdowns(geojson_path)
    files = sorted(routes_dir.glob("*.json"))

    updated = 0
    without_breakdown_before = 0
    still_without_breakdown = 0
    no_building = 0
    growth_total = 0
    growth_samples: list[tuple[str, int]] = []

    for path in files:
        try:
            building_id = int(path.stem)
        except ValueError:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        before = path.stat().st_size
        if "breakdown" not in payload:
            without_breakdown_before += 1

        breakdown = breakdowns.get(building_id)
        if breakdown is None:
            no_building += 1
            still_without_breakdown += 1
            continue
        if payload.get("breakdown") == breakdown:
            continue

        payload["breakdown"] = breakdown
        text = dump_routes(payload)
        path.write_text(text, encoding="utf-8")
        growth = len(text.encode("utf-8")) - before
        growth_total += growth
        updated += 1
        if len(growth_samples) < 5:
            growth_samples.append((path.name, growth))

    print(f"routes-файлов: {len(files):,}")
    print(f"обновлено: {updated:,}")
    print(f"без breakdown до мержа: {without_breakdown_before:,}")
    print(f"без breakdown после мержа: {still_without_breakdown:,}")
    if no_building:
        print(f"нет дома в geojson: {no_building:,}")
    if updated:
        print(
            "прирост: "
            + ", ".join(f"{name} +{growth} Б" for name, growth in growth_samples)
            + (f" …" if updated > len(growth_samples) else "")
        )
    print(f"прирост суммарно: {growth_total:,} Б")
    return 0


if __name__ == "__main__":
    sys.exit(main())
