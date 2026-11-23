#!/usr/bin/env python3
"""Компактный колоночный индекс жилых домов (V2-S2a).

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/build_index.py

Вход : data/districts/<osm_id>/buildings.geojson (только is_home)
Выход: data/index.json.gz
       {"v":1,"ids":[...],"lon":[...],"lat":[...],"addr":[...]}

Зачем: старый buildings-index.json — массив объектов, ~16 МБ на город.
Колоночный JSON + gzip укладывает те же данные в цель ≤3 МБ на 150 тыс.

Проверка: 50 случайных адресов из старых buildings-index.json трёх районов
должны находить тот же id в новом индексе.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISTRICTS = ROOT / "data" / "districts"
OUTPUT = ROOT / "data" / "index.json.gz"

SAMPLE_SIZE = 50
CITY_HOMES = 150_000
TARGET_BYTES = 3 * 1024 * 1024
SEED = 20261123

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tiles import representative_point  # noqa: E402


def district_dirs() -> list[Path]:
    dirs = [
        path
        for path in DISTRICTS.iterdir()
        if path.is_dir() and (path / "buildings.geojson").exists()
    ]
    return sorted(dirs, key=lambda path: int(path.name))


def collect_rows(dirs: list[Path]) -> tuple[list[tuple[int, float, float, str]], dict, int]:
    rows: list[tuple[int, float, float, str]] = []
    per_district = {}
    seen: set[int] = set()
    dropped = 0
    for district_dir in dirs:
        summary_path = district_dir / "summary.json"
        name = district_dir.name
        if summary_path.exists():
            name = json.loads(summary_path.read_text(encoding="utf-8"))["district"]["name"]
        features = json.loads(
            (district_dir / "buildings.geojson").read_text(encoding="utf-8")
        )["features"]
        local = []
        local_dropped = 0
        for feature in features:
            props = feature.get("properties") or {}
            if not props.get("is_home") or props.get("index") is None:
                continue
            building_id = int(props["id"])
            # Дома на границе районов попадают в два district-артефакта:
            # в индексе оставляем первое вхождение, остальные считаем дублями.
            if building_id in seen:
                dropped += 1
                local_dropped += 1
                continue
            seen.add(building_id)
            lon, lat = representative_point(feature["geometry"])
            local.append(
                (
                    building_id,
                    round(lon, 6),
                    round(lat, 6),
                    props.get("address") or "",
                )
            )
        rows.extend(local)
        per_district[district_dir.name] = {
            "name": name,
            "rows": len(local),
            "dropped": local_dropped,
            "gzip_bytes": len(pack(local)),
        }
    rows.sort(key=lambda row: row[0])
    return rows, per_district, dropped


def pack(rows: list[tuple[int, float, float, str]]) -> bytes:
    payload = {
        "v": 1,
        "ids": [row[0] for row in rows],
        "lon": [row[1] for row in rows],
        "lat": [row[2] for row in rows],
        "addr": [row[3] for row in rows],
    }
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def verify_old_index(rows: list[tuple[int, float, float, str]], dirs: list[Path]) -> dict:
    old: list[tuple[int, str]] = []
    for district_dir in dirs:
        path = district_dir / "buildings-index.json"
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            old.append((int(row[0]), row[3] or ""))
    if not old:
        return {"checked": 0, "matched": 0, "missing_old": 0}

    lookup: dict[str, set[int]] = {}
    for building_id, _lon, _lat, address in rows:
        lookup.setdefault(address, set()).add(building_id)

    rng = random.Random(SEED)
    sample = rng.sample(old, min(SAMPLE_SIZE, len(old)))
    matched = sum(1 for building_id, address in sample if building_id in lookup.get(address, ()))
    return {
        "checked": len(sample),
        "matched": matched,
        "missing_old": 0,
        "addresses_in_old": len(old),
    }


def human(size: int) -> str:
    return f"{size / 1024:.0f} KiB" if size < 1 << 20 else f"{size / (1 << 20):.2f} MiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args()

    dirs = district_dirs()
    if not dirs:
        raise SystemExit("Нет data/districts/*/buildings.geojson")
    rows, per_district, dropped = collect_rows(dirs)

    blob = pack(rows)
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)

    started = time.perf_counter()
    decoded = json.loads(gzip.decompress(out.read_bytes()))
    parse_ms = (time.perf_counter() - started) * 1000
    if len(decoded["ids"]) != len(rows):
        raise SystemExit("ОШИБКА: число id не совпадает после чтения")
    if len(set(decoded["ids"])) != len(decoded["ids"]):
        raise SystemExit("ОШИБКА: в индексе остались дубли id")

    extrapolated = len(blob) / max(1, len(rows)) * CITY_HOMES
    print(f"Индекс: {out.relative_to(ROOT)}")
    print(f"  домов: {len(rows):,} (уникальных id: {len(set(decoded['ids'])):,})")
    print(f"  дублей отброшено: {dropped:,}")
    for district_id, info in per_district.items():
        suffix = f", дублей {info['dropped']}" if info["dropped"] else ""
        print(
            f"    {district_id} {info['name']}: {info['rows']:,} домов, "
            f"{human(info['gzip_bytes'])} gzip{suffix}"
        )
    print(f"  размер: {human(len(blob))} ({len(blob):,} B) gzip")
    print(f"  на {CITY_HOMES:,} домов: {human(int(extrapolated))} "
          f"(цель <= {human(TARGET_BYTES)})")
    print(f"  разбор (gunzip + json.loads): {parse_ms:.0f} мс")

    ok = len(blob) <= TARGET_BYTES and extrapolated <= TARGET_BYTES
    if not args.no_verify:
        check = verify_old_index(rows, dirs)
        print(
            f"  сверка 50 адресов: {check['matched']}/{check['checked']} "
            f"(старых адресов: {check['addresses_in_old']:,})"
        )
        if check["checked"] and check["matched"] != check["checked"]:
            print("  ОШИБКА сверки адресов", file=sys.stderr)
            ok = False

    if not ok:
        print("СТОП: индекс не влезает в 3 МБ", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
