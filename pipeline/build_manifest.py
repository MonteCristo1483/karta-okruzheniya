#!/usr/bin/env python3
"""Манифест районов с размерами и sha256 (V2-S2a).

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/build_manifest.py

Выход: data/districts/manifest.json
Для каждого района: id, name, bbox и список файлов (здания, индекс, маршруты,
изохроны, подложка) с размером и sha256. Манифест — единственный источник
путей для фронтенда и деплоя шага 2b.

После записи манифест перечитывается и все sha256 проверяются заново.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISTRICTS = ROOT / "data" / "districts"
INDEX = ROOT / "data" / "index.json.gz"
ROUTES = ROOT / "data" / "routes"
ISOCHRONES = ROOT / "data" / "isochrones"
SHARDS = 16


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def walk_bbox(node, xs: list[float], ys: list[float]) -> None:
    if node and isinstance(node[0], (int, float)):
        xs.append(float(node[0]))
        ys.append(float(node[1]))
        return
    for child in node:
        walk_bbox(child, xs, ys)


def district_bbox(path: Path) -> list[float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    xs: list[float] = []
    ys: list[float] = []
    walk_bbox(payload["features"][0]["geometry"].get("coordinates") or [], xs, ys)
    return [min(xs), min(ys), max(xs), max(ys)]


def entry(role: str, path: Path, **extra) -> dict:
    item = {
        "role": role,
        "path": rel(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    item.update(extra)
    return item


def shard_entries(role: str, directory: Path, district_id: str) -> list[dict]:
    items = []
    for shard in range(SHARDS):
        path = directory / district_id / f"{shard}.json.gz"
        if path.exists():
            items.append(entry(role, path, shard=shard))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DISTRICTS / "manifest.json")
    args = parser.parse_args()

    if not INDEX.exists():
        print(f"Нет {INDEX}; сначала build_index.py", file=sys.stderr)
        return 1

    districts = []
    for district_dir in sorted(DISTRICTS.iterdir(), key=lambda p: p.name):
        summary_path = district_dir / "summary.json"
        district_path = district_dir / "district.geojson"
        if not district_dir.is_dir() or not summary_path.exists() or not district_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        district_id = str(summary["district"]["osm_id"])

        files: list[dict] = []
        for role, name in (
            ("buildings_geojson", "buildings.geojson"),
            ("buildings_pmtiles", "buildings.pmtiles"),
            ("basemap_geojson", "basemap.geojson"),
            ("basemap_pmtiles", "basemap.pmtiles"),
        ):
            path = district_dir / name
            if path.exists():
                files.append(entry(role, path))
        files.append(entry("index", INDEX))
        files.extend(shard_entries("routes", ROUTES, district_id))
        files.extend(shard_entries("isochrones", ISOCHRONES, district_id))

        districts.append(
            {
                "id": int(district_id),
                "name": summary["district"]["name"],
                "bbox": district_bbox(district_path),
                "files": files,
            }
        )

    manifest = {
        "v": 1,
        "index": entry("index", INDEX),
        "districts": districts,
    }
    out = args.out.resolve()
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Проверка: перечитываем манифест и сверяем каждый sha256.
    check = json.loads(out.read_text(encoding="utf-8"))
    checked = failed = 0
    for district in check["districts"]:
        for item in district["files"]:
            path = ROOT / item["path"]
            checked += 1
            if not path.exists() or sha256(path) != item["sha256"]:
                failed += 1
                print(f"  FAIL {item['path']}", file=sys.stderr)
        for role in ("routes", "isochrones"):
            shards = [item for item in district["files"] if item["role"] == role]
            if len(shards) != SHARDS:
                failed += 1
                print(
                    f"  FAIL {district['id']}: {role} {len(shards)}/{SHARDS} шардов",
                    file=sys.stderr,
                )

    print(f"Манифест: {rel(out)}")
    for district in check["districts"]:
        print(
            f"  {district['id']} {district['name']}: "
            f"{len(district['files'])} файлов, bbox={district['bbox']}"
        )
    print(f"  проверено файлов: {checked}, ошибок: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
