#!/usr/bin/env python3
"""Упаковка маршрутов и изохрон района в шарды (V2-S2a).

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/pack_district.py --district 1275551

Что делает:
  * берёт готовые `web/public/data/routes/<building_id>.json` и
    `web/public/data/isochrones/<building_id>.json` — ничего не пересчитывает;
  * оставляет только жилые дома района (is_home = TRUE в buildings.geojson);
  * пишет `data/routes/<district_id>/<shard>.json.gz` и
    `data/isochrones/<district_id>/<shard>.json.gz`, где shard = id % 16;
  * внутри шарда объект `{"<building_id>": <старый JSON>}`;
  * проверяет round-trip: 20 случайных id собираются обратно и сравниваются
    с исходным файлом по содержимому.

Исходные файлы routes/ и isochrones/ не удаляются и не изменяются.
Скрипт идемпотентен: повторный запуск перезаписывает только свои шарды.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DATA = ROOT / "web" / "public" / "data"
DISTRICTS = ROOT / "data" / "districts"
OUT_ROUTES = ROOT / "data" / "routes"
OUT_ISOCHRONES = ROOT / "data" / "isochrones"

SHARDS = 16
SAMPLE_SIZE = 20
SEED = 20261123


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_district(value: str) -> Path:
    """Каталог data/districts/<osm_id> по osm_id или имени района."""
    candidates = []
    for summary_path in sorted(DISTRICTS.glob("*/summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        district = summary.get("district") or {}
        candidates.append((summary_path.parent, int(district.get("osm_id", 0)),
                           str(district.get("name", ""))))
    if value.isdigit():
        for path, osm_id, _name in candidates:
            if osm_id == int(value):
                return path
    lowered = value.lower()
    for path, _osm_id, name in candidates:
        if lowered in name.lower():
            return path
    raise SystemExit(f"Район не найден в data/districts: {value}")


def load_home_ids(district_dir: Path) -> list[int]:
    path = district_dir / "buildings.geojson"
    if not path.exists():
        raise SystemExit(f"Нет {path}; сначала build_district.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = []
    for feature in payload["features"]:
        props = feature.get("properties") or {}
        if props.get("is_home") and props.get("index") is not None:
            ids.append(int(props["id"]))
    return sorted(set(ids))


def pack_layer(name: str, src_dir: Path, out_dir: Path, ids: list[int]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    shards: dict[int, dict[str, object]] = {shard: {} for shard in range(SHARDS)}
    sources: list[Path] = []
    missing = 0
    for building_id in ids:
        source = src_dir / f"{building_id}.json"
        if not source.exists():
            missing += 1
            continue
        sources.append(source)
        payload = json.loads(source.read_text(encoding="utf-8"))
        shards[building_id % SHARDS][str(building_id)] = payload

    before = {path: sha256(path) for path in sources}

    sizes = []
    for shard, entries in shards.items():
        raw = json.dumps(
            entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        blob = gzip.compress(raw, compresslevel=9, mtime=0)
        path = out_dir / f"{shard}.json.gz"
        path.write_bytes(blob)
        sizes.append((shard, len(blob), len(entries)))

    after = {path: sha256(path) for path in sources}
    changed = sum(1 for path in sources if before[path] != after[path])

    return {
        "layer": name,
        "ids": len(ids),
        "packed": len(sources),
        "missing_sources": missing,
        "files": len(sizes),
        "bytes": sum(size for _shard, size, _count in sizes),
        "max_shard_bytes": max((size for _shard, size, _count in sizes), default=0),
        "empty_shards": sum(1 for _shard, _size, count in sizes if count == 0),
        "sources_changed": changed,
        "out_dir": out_dir,
        "sources": sources,
    }


def verify_layer(name: str, src_dir: Path, out_dir: Path, ids: list[int]) -> dict:
    ids = [building_id for building_id in ids if (src_dir / f"{building_id}.json").exists()]
    if not ids:
        empty = all(
            json.loads(gzip.decompress((out_dir / f"{s}.json.gz").read_bytes())) == {}
            for s in range(SHARDS)
        )
        return {"layer": name, "checked": 0, "matched": 0, "empty_ok": empty}

    rng = random.Random(SEED)
    sample = rng.sample(ids, min(SAMPLE_SIZE, len(ids)))
    matched = 0
    details = []
    for building_id in sample:
        path = out_dir / f"{building_id % SHARDS}.json.gz"
        shard = json.loads(gzip.decompress(path.read_bytes()))
        got = shard.get(str(building_id))
        expected = json.loads((src_dir / f"{building_id}.json").read_text(encoding="utf-8"))
        ok = got == expected
        matched += int(ok)
        details.append({"id": building_id, "ok": ok})
    return {
        "layer": name,
        "checked": len(sample),
        "matched": matched,
        "empty_ok": True,
        "details": details,
    }


def human(size: int) -> str:
    return f"{size / 1024:.0f} KiB" if size < 1 << 20 else f"{size / (1 << 20):.2f} MiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--district", required=True, help="osm_id района или имя")
    args = parser.parse_args()

    district_dir = resolve_district(args.district)
    district_id = district_dir.name
    summary = json.loads((district_dir / "summary.json").read_text(encoding="utf-8"))
    homes = load_home_ids(district_dir)
    print(f"Район: {summary['district']['name']} (osm_id={district_id}), жилых: {len(homes):,}")

    layers = []
    for name, src_name, out_root in (
        ("routes", "routes", OUT_ROUTES),
        ("isochrones", "isochrones", OUT_ISOCHRONES),
    ):
        src_dir = PUBLIC_DATA / src_name
        out_dir = out_root / district_id
        stats = pack_layer(name, src_dir, out_dir, homes)
        check = verify_layer(name, src_dir, out_dir, homes)
        layers.append({"stats": stats, "verify": check})
        print(
            f"  {name}: {stats['packed']:,}/{stats['ids']:,} упаковано, "
            f"{stats['files']} шардов, {human(stats['bytes'])}, "
            f"макс шард {human(stats['max_shard_bytes'])}, "
            f"пустых {stats['empty_shards']}, "
            f"round-trip {check['matched']}/{check['checked']}, "
            f"исходники изменены: {stats['sources_changed']}"
        )
        if stats["missing_sources"]:
            print(f"    нет исходников: {stats['missing_sources']:,}")
        if check["checked"] and check["matched"] != check["checked"]:
            print("    ОШИБКА round-trip", file=sys.stderr)
            return 1
        if not check["empty_ok"]:
            print("    ОШИБКА: пустые шарды не читаются", file=sys.stderr)
            return 1
        if stats["sources_changed"]:
            print("    ОШИБКА: исходные файлы изменились", file=sys.stderr)
            return 1

    total = sum(item["stats"]["bytes"] for item in layers)
    print(f"Итого шардов: {sum(item['stats']['files'] for item in layers)}, "
          f"{human(total)} gzip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
