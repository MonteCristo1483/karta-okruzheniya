#!/usr/bin/env python
"""Build PMTiles for Presnya buildings and the compact search index.

Input : web/public/data/presnya_buildings.geojson (gitignored artifact)
Output: web/public/data/tiles/buildings-<sha8>.pmtiles
        web/public/data/buildings-index.json
                rows: [id, lon, lat, address, index, year,
                       road, railway, construction, industrial]
                (last four = penalty distance percentiles, 3 decimals or null)

tippecanoe is pinned to 2.79.0. There is no apt package and felt ships no
release binaries; ghcr.io/felt/tippecanoe is not pullable ("denied"), so the
binary is built from source with pipeline/docker/tippecanoe.Dockerfile.
Repeated runs on the same input give the same tile set (+-5% by size).

Stdlib only. Nothing in web/app, workflows or export_map_data.py is touched.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import math
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "web" / "public" / "data"
BUILDINGS = DATA_DIR / "presnya_buildings.geojson"
TILES_DIR = DATA_DIR / "tiles"
INDEX = DATA_DIR / "buildings-index.json"

DOCKER_IMAGE = "karta-tippecanoe:2.79.0"
TIPPECANOE_VERSION = "2.79.0"
LAYER = "buildings"

# Attributes written to tiles. breakdown is an object in geojson; MVT has no
# objects, so it travels as a JSON string.
TILE_ATTRS = ("id", "index", "height", "address", "year")

# Tiles-B: единый feature-filter (tippecanoe применяет к слою только его).
# TILES-SEAMLESS: maxzoom теперь z14, поэтому breakdown пишем с z13, а дома
# ниже 9 м прореживаем только на настоящем обзоре (z9-12): на z13-14 нужны
# все 100% зданий, иначе при перелёте с z13 на z14 из пустоты выскакивают
# мелкие дома и кварталы выглядят «плешивыми».
TILE_FILTER = json.dumps({
    "*": [
        "all",
        ["attribute-filter", "breakdown", [">=", "$zoom", 13]],
        ["any", [">=", "$zoom", 13], [">=", "height", 9]],
    ]
}, separators=(",", ":"))

# Index row layout (buildings-index.json), one array per building:
#   [id, lon, lat, address, index, year, road, railway, construction, industrial]
# The last four are percentiles of the penalty distance distribution, computed
# exactly like percentileFor() in web/app/map.tsx: share of district buildings
# farther from the object (0..1), rounded to 3 decimals; null when the building
# has no distance_m for that kind.
FIELDS = (
    "id", "lon", "lat", "address", "index", "year",
    "road", "railway", "construction", "industrial",
)

# 64091236 is the reference building from the brief; the other four are picked
# deterministically from the data to cover edge cases (tallest, lowest score,
# highest score, oldest year).
REFERENCE_ID = 64091236

PENALTY_KINDS = ("road", "railway", "construction", "industrial")


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #

def load_buildings(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["features"]


def sha8(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


# --------------------------------------------------------------------------- #
# index geometry
# --------------------------------------------------------------------------- #

def _ring_stats(ring: list[list[float]]) -> tuple[float, float, float]:
    """Signed area and area centroid of a closed ring."""
    area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area *= 0.5
    if area == 0:
        return 0.0, ring[0][0], ring[0][1]
    return area, cx / (6.0 * area), cy / (6.0 * area)


def _polygon_stats(rings: list[list[list[float]]]) -> tuple[float, float, float]:
    outer_area, cx, cy = _ring_stats(rings[0])
    total = abs(outer_area)
    cx_w, cy_w = abs(outer_area) * cx, abs(outer_area) * cy
    for hole in rings[1:]:
        hole_area, hx, hy = _ring_stats(hole)
        total -= abs(hole_area)
        cx_w -= abs(hole_area) * hx
        cy_w -= abs(hole_area) * hy
    if total <= 0:
        return abs(outer_area), cx, cy
    return total, cx_w / total, cy_w / total


def representative_point(geometry: dict) -> tuple[float, float]:
    """Area centroid of the largest polygon (bbox centre as a last resort)."""
    kind = geometry.get("type")
    if kind == "Polygon":
        _, lon, lat = _polygon_stats(geometry["coordinates"])
        return lon, lat
    if kind == "MultiPolygon":
        best = max(
            (_polygon_stats(poly) for poly in geometry["coordinates"]),
            key=lambda item: item[0],
        )
        return best[1], best[2]
    coords: list[tuple[float, float]] = []

    def walk(node):
        if node and isinstance(node[0], (int, float)):
            coords.append((node[0], node[1]))
            return
        for child in node:
            walk(child)

    walk(geometry.get("coordinates") or [])
    if not coords:
        return 0.0, 0.0
    return (
        sum(c[0] for c in coords) / len(coords),
        sum(c[1] for c in coords) / len(coords),
    )


def penalty_distances(features: list[dict]) -> dict[str, list[float]]:
    """distance_m for every building per kind; missing counts as +inf, as in the client."""
    values = {kind: [] for kind in PENALTY_KINDS}
    for feature in features:
        penalties = (feature["properties"].get("breakdown") or {}).get("penalties") or {}
        for kind in PENALTY_KINDS:
            distance = (penalties.get(kind) or {}).get("distance_m")
            values[kind].append(math.inf if distance is None else float(distance))
    return values


def build_index(features: list[dict]) -> list[list]:
    # Same distribution as web/app/map.tsx percentileFor(): buildings without a
    # distance count as +inf, i.e. "farther", and the percentile is the share of
    # buildings strictly farther than this one.
    sorted_distances = {
        kind: sorted(values) for kind, values in penalty_distances(features).items()
    }

    def percentile(kind: str, distance: float | None) -> float | None:
        if distance is None:
            return None
        values = sorted_distances[kind]
        if not values:
            return 0.0
        farther = len(values) - bisect.bisect_right(values, float(distance))
        return round(farther / len(values), 3)

    rows = []
    for feature in features:
        props = feature["properties"]
        lon, lat = representative_point(feature["geometry"])
        penalties = (props.get("breakdown") or {}).get("penalties") or {}
        row = [
            props["id"],
            round(lon, 6),
            round(lat, 6),
            props.get("address"),
            props.get("index"),
            props.get("year"),
        ]
        for kind in PENALTY_KINDS:
            row.append(percentile(kind, (penalties.get(kind) or {}).get("distance_m")))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# tippecanoe
# --------------------------------------------------------------------------- #

def write_ndjson(features: list[dict], path: Path, with_breakdown: bool = True) -> int:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for feature in features:
            props = feature["properties"]
            out = {key: props.get(key) for key in TILE_ATTRS}
            breakdown = props.get("breakdown")
            if with_breakdown and breakdown is not None:
                out["breakdown"] = json.dumps(
                    breakdown, ensure_ascii=False, separators=(",", ":")
                )
            fh.write(json.dumps(
                {"type": "Feature", "properties": out,
                 "geometry": feature["geometry"]},
                ensure_ascii=False, separators=(",", ":"),
            ))
            fh.write("\n")
    return sum(1 for _ in path.open(encoding="utf-8"))


def run_tippecanoe(ndjson: Path, out: Path) -> None:
    rel_in = ndjson.relative_to(ROOT).as_posix()
    rel_out = out.relative_to(ROOT).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ROOT}:/work",
        "-w", "/work",
        DOCKER_IMAGE,
        "--force",
        "-o", f"/work/{rel_out}",
        "-Z9", "-z14", "-d14",
        # Tiles-B (бюджет z13-14). Один -j, потому что tippecanoe применяет
        # к слою только последний фильтр: несколько -j затирают друг друга.
        # Внутри "all" два условия:
        # 1) attribute-LOD (LOD-A): breakdown ниже z13 не нужен — карточка
        #    открывается на z16.6 и берёт разбивку из z14-тайла/шарда;
        # 2) height-LOD для обзора: ниже z13 оставляем дома от 9 м, иначе
        #    z9-12 не влезают в бюджет. z13-z14 не трогаем: там дома нужны
        #    все (кадр 16.6 масштабируется из z14 без потерь).
        "-j", TILE_FILTER,
        # -an режет самые мелкие полигоны, -as (--drop-densest-as-needed)
        # добавляет прореживание по плотности; оба срабатывают только в
        # тайлах сверх лимита. -r1 обязателен: иначе фоновое прореживание
        # по -r2.5 режет ~60% домов даже на z13-z14 (502 КБ → 6 КБ).
        # TILES-SEAMLESS: лимит поднят 220000 → 450000, чтобы на z12-14
        # --drop-densest-as-needed не выбивал дома в плотных кварталах центра.
        "--drop-smallest-as-needed", "--drop-densest-as-needed", "-r1",
        "--maximum-tile-bytes", "450000",
        "--no-tile-stats",
        "-l", LAYER,
        "--use-attribute-for-id=id",
        f"/work/{rel_in}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError(f"tippecanoe failed with code {proc.returncode}")
    tail = [line for line in proc.stderr.splitlines() if line.strip()]
    for line in tail[-6:]:
        print(f"  tippecanoe: {line}")


# --------------------------------------------------------------------------- #
# pmtiles reader (only what the report needs: tile count and sizes)
# --------------------------------------------------------------------------- #

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _read_directory(blob: bytes, compression: int) -> list[tuple[int, int, int, int]]:
    """Decode a PMTiles v3 directory (columnar varints)."""
    if compression == 2:  # gzip
        blob = gzip.decompress(blob)
    elif compression != 1:
        raise RuntimeError(f"unsupported pmtiles compression {compression}")

    pos = 0
    count, pos = _varint(blob, pos)
    tile_ids, run_lengths, lengths, offsets = [], [], [], []
    for _ in range(count):
        delta, pos = _varint(blob, pos)
        tile_ids.append((tile_ids[-1] if tile_ids else 0) + delta)
    for _ in range(count):
        value, pos = _varint(blob, pos)
        run_lengths.append(value)
    for _ in range(count):
        value, pos = _varint(blob, pos)
        lengths.append(value)
    for index in range(count):
        value, pos = _varint(blob, pos)
        if index > 0 and value == 0:
            offsets.append(offsets[-1] + lengths[-1])
        else:
            offsets.append(value - 1)
    return list(zip(tile_ids, run_lengths, lengths, offsets))


def zoom_of(tile_id: int) -> int:
    # Tile ids are grouped by zoom: zoom z starts at (4**z - 1) // 3.
    z = 0
    while (4 ** (z + 1) - 1) // 3 <= tile_id:
        z += 1
    return z


def pmtiles_stats(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:7] != b"PMTiles" or raw[7] != 3:
        raise RuntimeError("not a PMTiles v3 file")
    (root_off, root_len, _meta_off, _meta_len,
     leaf_off, leaf_len, _data_off, _data_len) = struct.unpack_from("<8Q", raw, 8)
    # Заголовок — источник истины по числу тайлов. Директории (root/leaf)
    # разбираем только ради per_zoom; на больших городах (листья + несколько
    # gzip-членов) разбор может не сойтись, и это не должно ронять отчёт.
    addressed, _entries, _contents = struct.unpack_from("<3Q", raw, 72)
    internal = raw[97]
    min_zoom, max_zoom = raw[100], raw[101]

    per_zoom: dict[int, list[int]] = {}
    try:
        entries = _read_directory(raw[root_off:root_off + root_len], internal)
        leaf_entries = _read_directory(raw[leaf_off:leaf_off + leaf_len], internal) \
            if leaf_len else []
        for tile_id, run_length, length, offset in entries:
            if run_length == 0:  # pointer into the leaf directory
                blob = raw[leaf_off + offset:leaf_off + offset + length]
                leaf_entries.extend(_read_directory(blob, internal))
        all_entries = [e for e in entries + leaf_entries if e[1] != 0]
        for tile_id, run_length, length, _offset in all_entries:
            per_zoom.setdefault(zoom_of(tile_id), []).extend([length] * run_length)
    except (OSError, EOFError, IndexError, struct.error):
        per_zoom = {}

    parsed = sum(len(v) for v in per_zoom.values())
    return {
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "tiles": parsed or addressed,
        "addressed_tiles": addressed,
        "per_zoom": {
            z: {"count": len(v), "bytes": sum(v), "max": max(v)}
            for z, v in sorted(per_zoom.items())
        },
    }


# --------------------------------------------------------------------------- #
# verification against the source geojson
# --------------------------------------------------------------------------- #

def control_ids(features: list[dict]) -> list[int]:
    picks = [REFERENCE_ID]
    with_height = [f for f in features if f["properties"].get("height") is not None]
    picks.append(max(with_height, key=lambda f: f["properties"]["height"])["properties"]["id"])
    picks.append(min(features, key=lambda f: f["properties"]["index"])["properties"]["id"])
    picks.append(max(features, key=lambda f: f["properties"]["index"])["properties"]["id"])
    with_year = [f for f in features if f["properties"].get("year") is not None]
    picks.append(min(with_year, key=lambda f: f["properties"]["year"])["properties"]["id"])

    # keep order, drop duplicates
    seen, ordered = set(), []
    for fid in picks:
        if fid not in seen:
            seen.add(fid)
            ordered.append(fid)
    # pad with evenly spaced buildings if the data collapsed some picks
    if len(ordered) < 5:
        step = max(1, len(features) // 5)
        for feature in features[::step]:
            fid = feature["properties"]["id"]
            if fid not in seen:
                seen.add(fid)
                ordered.append(fid)
            if len(ordered) == 5:
                break
    return ordered[:5]


def decode_tiles(pmtiles: Path) -> dict[int, dict]:
    rel = pmtiles.relative_to(ROOT).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ROOT}:/work",
        "-w", "/work",
        "--entrypoint", "tippecanoe-decode",
        DOCKER_IMAGE,
        f"/work/{rel}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError("tippecanoe-decode failed")

    found: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        # tippecanoe-decode pretty-prints one Feature per line, comma-separated.
        line = line.strip().rstrip(",")
        if not line.startswith('{ "type": "Feature"'):
            continue
        feature = json.loads(line)
        props = feature.get("properties") or {}
        fid = feature.get("id", props.get("id"))
        if fid is not None:
            found[int(fid)] = props
    return found


def verify(pmtiles: Path, features: list[dict],
           check_breakdown: bool = True) -> tuple[list[dict], dict]:
    source = {f["properties"]["id"]: f["properties"] for f in features}
    wanted = control_ids(features)
    tiled = decode_tiles(pmtiles)

    rows, ok = [], True
    for fid in wanted:
        src = source[fid]
        got = tiled.get(fid)
        diffs = []
        if got is None:
            diffs.append("missing from tiles")
            got = {}
        else:
            got = dict(got)
            got.setdefault("id", fid)  # --use-attribute-for-id: id lives on the feature
            for key in TILE_ATTRS:  # tiles carry tile attributes, not index fields
                a, b = src.get(key), got.get(key)
                if key in ("index", "height") and a is not None and b is not None:
                    if not math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-6):
                        diffs.append(f"{key}: {a!r} != {b!r}")
                elif key == "year" and a is not None and b is not None:
                    if int(a) != int(b):
                        diffs.append(f"{key}: {a!r} != {b!r}")
                elif a != b:
                    diffs.append(f"{key}: {a!r} != {b!r}")
            breakdown = got.get("breakdown")
            if check_breakdown and src.get("breakdown") is not None:
                try:
                    if json.loads(breakdown) != src["breakdown"]:
                        diffs.append("breakdown: JSON differs")
                except (TypeError, ValueError):
                    diffs.append("breakdown: not valid JSON")
        ok = ok and not diffs
        rows.append({
            "id": fid,
            "ok": not diffs,
            "index": src.get("index"),
            "height": src.get("height"),
            "address": src.get("address"),
            "year": src.get("year"),
            "diffs": diffs,
        })
    return rows, {"ok": ok and len(tiled) > 0, "features_in_tiles": len(tiled)}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def human_size(size: int) -> str:
    return f"{size / 1024:.0f} KiB" if size < 1 << 20 else f"{size / (1 << 20):.2f} MiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--geojson", type=Path, default=BUILDINGS)
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument(
        "--without-breakdown", action="store_true",
        help="measure the lean variant: breakdown is served from routes/<id>.json",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    source = args.geojson.resolve()
    if not source.exists():
        raise SystemExit(f"missing input: {source} (copy it from karta-web, see docstring)")

    features = load_buildings(source)
    digest = sha8(source)
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "-nobreakdown" if args.without_breakdown else ""
    pmtiles = TILES_DIR / f"buildings-{digest}{suffix}.pmtiles"
    print(f"input : {source.name} - {len(features):,} buildings, sha256[:8]={digest}")

    build_started = time.perf_counter()
    staged = ROOT / ".build_tiles_tmp.ndjson"
    try:
        n = write_ndjson(features, staged, with_breakdown=not args.without_breakdown)
        print(f"ndjson: {n:,} features, {human_size(staged.stat().st_size)}")
        run_tippecanoe(staged, pmtiles)
    finally:
        staged.unlink(missing_ok=True)
    build_seconds = time.perf_counter() - build_started

    index_rows = build_index(features)
    INDEX.write_text(
        json.dumps(index_rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    pmtiles_bytes = pmtiles.read_bytes()
    index_bytes = INDEX.read_bytes()
    stats = pmtiles_stats(pmtiles)
    zmax = stats["per_zoom"].get(stats["max_zoom"], {"count": 0, "bytes": 0, "max": 0})

    report = {
        "pmtiles": str(pmtiles.relative_to(ROOT)),
        "pmtiles_bytes": len(pmtiles_bytes),
        "pmtiles_gzip_bytes": len(gzip.compress(pmtiles_bytes, 9)),
        "tiles": stats["tiles"],
        "tile_bytes": sum(v["bytes"] for v in stats["per_zoom"].values()),
        "tiles_per_zoom": stats["per_zoom"],
        "z14_avg_bytes": zmax["bytes"] // zmax["count"] if zmax["count"] else 0,
        "z14_max_bytes": zmax["max"],
        "breakdown_in_tiles": not args.without_breakdown,
        "index": str(INDEX.relative_to(ROOT)),
        "index_rows": len(index_rows),
        "index_bytes": len(index_bytes),
        "index_gzip_bytes": len(gzip.compress(index_bytes, 9)),
        "build_seconds": round(build_seconds, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
    }

    verification = None
    if not args.no_verify:
        rows, summary = verify(pmtiles, features,
                               check_breakdown=not args.without_breakdown)
        verification = {"summary": summary, "buildings": rows}
        report["verify_ok"] = summary["ok"]

    print()
    print(f"pmtiles      : {report['pmtiles']}")
    gz_note = "no gain, tiles are already gzip" if report["pmtiles_gzip_bytes"] >= report["pmtiles_bytes"] \
        else human_size(report["pmtiles_gzip_bytes"])
    print(f"  size       : {human_size(report['pmtiles_bytes'])} "
          f"({report['pmtiles_bytes']:,} B); gzip {gz_note}")
    print(f"  tiles      : {report['tiles']:,} "
          f"(z{stats['min_zoom']}-z{stats['max_zoom']}), payload {human_size(report['tile_bytes'])}")
    print(f"  z{stats['max_zoom']:<9}: {zmax['count']} tiles, avg {report['z14_avg_bytes']:,} B, "
          f"max {report['z14_max_bytes']:,} B")
    print(f"index        : {report['index']} - {len(index_rows):,} rows, "
          f"{human_size(report['index_bytes'])}, gzip {human_size(report['index_gzip_bytes'])}")
    print(f"build time   : {report['build_seconds']} s (total {report['total_seconds']} s)")
    if verification:
        print(f"verify       : {'OK' if verification['summary']['ok'] else 'FAIL'} "
              f"({verification['summary']['features_in_tiles']:,} features decoded from tiles)")
        for row in verification["buildings"]:
            mark = "ok  " if row["ok"] else "FAIL"
            print(f"  {mark} {row['id']}  index={row['index']} height={row['height']} "
                  f"year={row['year']} address={row['address']!r}")
            for diff in row["diffs"]:
                print(f"         {diff}")

    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = verification is None or verification["summary"]["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
