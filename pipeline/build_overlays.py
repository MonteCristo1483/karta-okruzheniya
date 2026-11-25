#!/usr/bin/env python3
"""Единый городской overlays.pmtiles: redzones + poi + trees (TILES-OVERLAYS).

Зачем: раньше красные зоны, POI и деревья грузились по 132 районам, из-за
чего Москва подгружалась кусками по границам районов, а на обзоре z9–11
красные магистрали были выключены. Теперь три слоя лежат в одном модульном
файле `data/overlays.pmtiles` (z8–14):

    redzones — линии магистралей и ж/д из `linear` (trunk/primary/secondary,
               railway) + motorway, съезды развязок и все tertiary из PBF одним
               проходом; режем не по районам, а по рамке
               Москвы (+0.02°), линии берём целиком — без дублей и обрывов на
               границах; промзоны и стройки — полигоны `zones` с z13;
               LOD: motorway/motorway_link/trunk/trunk_link/primary/
               primary_link/railway — z8+, secondary/secondary_link/tertiary —
               z12+ (tertiary — все, независимо от lanes: basemap рисует их как
               major, иначе в красной магистрали остаются серые дыры);
    poi      — `poi` в рамках скоринга (магазины — только subtype из
               config/scoring.json, как в export_map_data.py);
    trees    — декоративные кроны внутри полигонов green (park/wood):
               детерминированный PRNG как в buildTreePoints (web/app/map.tsx),
               60–78 точек/га, лимит 220 на полигон, area >= 0.04 га.
               Полигоны берём из уже собранного `data/basemap.pmtiles`
               (z14: полный набор зелени без нарезки по районам и без
               повторного скана PBF на 885 МБ).

Использование:
    pipeline/.venv/Scripts/python.exe pipeline/build_overlays.py
    pipeline/.venv/Scripts/python.exe pipeline/build_overlays.py --no-copy
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import struct
import subprocess
import sys
from pathlib import Path

import osmium
import psycopg
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon, mapping, shape
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_tiles import human_size, pmtiles_stats  # noqa: E402
from db import DB_DSN  # noqa: E402

CITY_DIR = ROOT / "data" / "city"
DEFAULT_OUT = ROOT / "data" / "overlays.pmtiles"
BASEMAP = ROOT / "data" / "basemap.pmtiles"
PBF_PATH = ROOT / "data" / "raw" / "moscow.osm.pbf"
WEB_OUT = ROOT / "web" / "public" / "data" / "overlays.pmtiles"
CONFIG_PATH = ROOT / "config" / "scoring.json"
DOCKER_IMAGE = "karta-tippecanoe:2.79.0"

# Красные зоны забираем с запасом вокруг Москвы: линии берём целиком, поэтому
# на границе они законно выходят за контур города.
MOSCOW_MARGIN = 0.02
GREEN_ZOOM = 14  # зелень из подложки: все park/wood, полигоны уже без швов районов
TREE_MIN_ZOOM = 14
TREE_LIMIT_PER_POLYGON = 220
TREE_MIN_AREA_HA = 0.04
TREE_DENSITY = {"wood": 78, "park": 70}

LINEAR_MAJOR_QUERY = """
SELECT DISTINCT kind, class, ST_AsGeoJSON(geometry)
FROM linear
WHERE (
        (kind = 'road' AND class IN ('trunk', 'primary'))
        OR (kind = 'railway' AND class IN ('rail', 'narrow_gauge', 'light_rail'))
      )
  AND ST_Intersects(geometry, (SELECT ST_Buffer(geometry, %s) FROM moscow))
"""

LINEAR_SECONDARY_QUERY = """
SELECT DISTINCT kind, class, ST_AsGeoJSON(geometry)
FROM linear
WHERE kind = 'road' AND class = 'secondary'
  AND ST_Intersects(geometry, (SELECT ST_Buffer(geometry, %s) FROM moscow))
"""

# В PostGIS `linear` лежат только трёхклассовые магистрали из SPEC §3 (их
# использует скоринг). Для отрисовки красных зон нужны ещё motorway, съезды
# развязок и все tertiary (basemap рисует tertiary как major; фильтр по lanes
# оставлял серые дыры посреди красных магистралей) — берём их из PBF одним
# проходом, чтобы не менять `linear` (иначе поедут golden-тесты скоринга).
PBF_MIN_ZOOM8 = ("motorway", "motorway_link", "trunk_link", "primary_link")
PBF_MIN_ZOOM12 = ("secondary_link",)

ZONES_QUERY = """
SELECT DISTINCT kind, ST_AsGeoJSON(geometry)
FROM zones
WHERE kind IN ('industrial', 'construction')
  AND ST_Intersects(geometry, (SELECT ST_Buffer(geometry, %s) FROM moscow))
"""

POI_QUERY = """
SELECT DISTINCT type, subtype, name, ST_AsGeoJSON(geometry)
FROM poi
WHERE (type IN ('school', 'kindergarten', 'park', 'subway_entrance', 'rail_platform')
       OR (type = 'shop' AND subtype = ANY(%s)))
  AND ST_Intersects(geometry, (SELECT geometry FROM moscow))
"""

POI_TRANSIT = {"subway_entrance", "rail_platform"}


# --------------------------------------------------------------------------- #
# красные зоны и POI из PostGIS
# --------------------------------------------------------------------------- #

def moscow_shape(cur):
    """Контур Москвы: рамку города берём из БД, а не из констант."""
    cur.execute("SELECT ST_AsGeoJSON(geometry) FROM moscow")
    return shape(json.loads(cur.fetchone()[0]))


def line_feature(kind: str, cls: str, geometry: dict, minzoom: int) -> dict:
    return {
        "type": "Feature",
        "tippecanoe": {"layer": "redzones", "minzoom": minzoom},
        "properties": {"kind": kind, "class": cls},
        "geometry": geometry,
    }


def zone_feature(kind: str, geom_json: str) -> dict:
    return {
        "type": "Feature",
        "tippecanoe": {"layer": "redzones", "minzoom": 13},
        "properties": {"kind": kind},
        "geometry": json.loads(geom_json),
    }


def poi_feature(poi_type: str, subtype, name, geom_json: str) -> dict:
    return {
        "type": "Feature",
        "tippecanoe": {
            "layer": "poi",
            "minzoom": 11 if poi_type in POI_TRANSIT else 13,
        },
        "properties": {"type": poi_type, "subtype": subtype, "name": name},
        "geometry": json.loads(geom_json),
    }


def collect_redzones(cur) -> list[dict]:
    features: list[dict] = []
    cur.execute(LINEAR_MAJOR_QUERY, [MOSCOW_MARGIN])
    features += [line_feature(k, c, json.loads(g), 8) for k, c, g in cur.fetchall()]
    cur.execute(LINEAR_SECONDARY_QUERY, [MOSCOW_MARGIN])
    features += [line_feature(k, c, json.loads(g), 12) for k, c, g in cur.fetchall()]
    cur.execute(ZONES_QUERY, [MOSCOW_MARGIN])
    features += [zone_feature(k, g) for k, g in cur.fetchall()]
    return features


# --------------------------------------------------------------------------- #
# недостающие классы магистралей из PBF (motorway, съезды, все tertiary)
# --------------------------------------------------------------------------- #

class MissingRoadsCollector(osmium.SimpleHandler):
    """Один проход по PBF: нужные highway внутри рамки Москвы (+запас)."""

    def __init__(self, area):
        super().__init__()
        self.scope = prep(area)
        self.wkbfab = osmium.geom.WKBFactory()
        self.features: list[dict] = []
        self.counts: dict[str, int] = {}
        self.skipped = 0

    def way(self, w: osmium.osm.Way) -> None:
        highway = w.tags.get("highway")
        if highway in PBF_MIN_ZOOM8:
            minzoom = 8
        elif highway in PBF_MIN_ZOOM12:
            minzoom = 12
        elif highway == "tertiary":
            minzoom = 12
        else:
            return
        try:
            wkb_hex = self.wkbfab.create_linestring(w)
            geom = shapely_wkb.loads(bytes.fromhex(wkb_hex))
        except (RuntimeError, ValueError):
            self.skipped += 1
            return
        if geom.is_empty or not self.scope.intersects(geom):
            return
        self.features.append(line_feature("road", highway, mapping(geom), minzoom))
        self.counts[highway] = self.counts.get(highway, 0) + 1


def collect_pbf_roads(pbf: Path, moscow_geom) -> tuple[list[dict], dict[str, int]]:
    """motorway/съезды/z12-классы из PBF — без записи в PostGIS `linear`."""
    if not pbf.is_file():
        raise SystemExit(f"нет {pbf}: без PBF не собрать полные классы магистралей")
    # pyosmium на Windows не открывает PBF по абсолютному пути с кириллицей
    # (см. build_basemap.py): передаём путь относительно cwd — он ASCII.
    try:
        open_arg = pbf.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        open_arg = str(pbf)
    if not open_arg.isascii():
        raise SystemExit(
            f"путь к PBF содержит не-ASCII символы ({open_arg}): pyosmium его не "
            "откроет, запустите скрипт из корня репозитория"
        )
    area = moscow_geom.buffer(MOSCOW_MARGIN)
    collector = MissingRoadsCollector(area)
    collector.apply_file(open_arg, locations=True)
    return collector.features, dict(sorted(collector.counts.items()))


def collect_poi(cur, shop_subtypes: list[str]) -> list[dict]:
    cur.execute(POI_QUERY, [shop_subtypes])
    return [poi_feature(t, st, name, g) for t, st, name, g in cur.fetchall()]


# --------------------------------------------------------------------------- #
# деревья: тот же детерминированный PRNG, что в buildTreePoints
# --------------------------------------------------------------------------- #

def ring_area_hectares(ring: list[list[float]]) -> float:
    """Площадь кольца в гектарах — как ringAreaHectares() в web/app/map.tsx."""
    if len(ring) < 3:
        return 0.0
    lat_rad = 55.756 * math.pi / 180
    lon_m = math.cos(lat_rad) * 111320
    lat_m = 111132
    total = 0.0
    prev = ring[-1]
    for point in ring:
        total += (prev[0] - point[0]) * lon_m * ((prev[1] + point[1]) * 0.5 * lat_m)
        prev = point
    return abs(total) / 10000


def ring_contains(point: list[float], ring: list[list[float]]) -> bool:
    """Ray casting — как ringContains() в web/app/map.tsx."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        cross = (yi > point[1]) != (yj > point[1]) and point[0] < (
            (xj - xi) * (point[1] - yi) / ((yj - yi) or 1e-12) + xi
        )
        if cross:
            inside = not inside
        j = i
    return inside


def polygon_contains(point: list[float], rings: list[list[list[float]]]) -> bool:
    if not ring_contains(point, rings[0]):
        return False
    return not any(ring_contains(point, hole) for hole in rings[1:])


def _js_round(value: float) -> int:
    """Math.round(): половина вверх, а не банковское округление Python."""
    return int(math.floor(value + 0.5))


def tree_points(
    cls: str,
    rings: list[list[list[float]]],
    feature_index: int,
    part_index: int,
) -> list[dict]:
    """Кроны одного полигона: PRNG 1:1 как в buildTreePoints(features)."""
    are_ha = ring_area_hectares(rings[0])
    if are_ha < TREE_MIN_AREA_HA:
        return []
    xs = [point[0] for point in rings[0]]
    ys = [point[1] for point in rings[0]]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    seed = (
        (_js_round(min_x * 1e5) * 73856093)
        ^ (_js_round(min_y * 1e5) * 19349663)
        ^ ((feature_index + 1) * 83492791)
        ^ (part_index * 2654435761)
    ) & 0xFFFFFFFF

    def rand() -> float:
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        return seed / 4294967296

    density = TREE_DENSITY[cls]
    want = min(TREE_LIMIT_PER_POLYGON, max(1, round(are_ha * density)))
    tone_threshold = 0.45 if cls == "wood" else 0.3
    points: list[dict] = []
    tries = 0
    while len(points) < want and tries < want * 9:
        tries += 1
        point = [min_x + rand() * (max_x - min_x), min_y + rand() * (max_y - min_y)]
        if not polygon_contains(point, rings):
            continue
        size = _js_round((0.78 + rand() * 0.62) * 100) / 100
        tone = 1 if rand() < tone_threshold else 0
        points.append(
            {
                "type": "Feature",
                "tippecanoe": {"layer": "trees", "minzoom": TREE_MIN_ZOOM},
                "properties": {"size": size, "tone": tone},
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(point[0], 6), round(point[1], 6)],
                },
            }
        )
    return points


def decode_green(path: Path) -> list[dict]:
    """Полигоны green (park/wood) из городской подложки на одном зуме."""
    rel = path.relative_to(ROOT).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ROOT}:/work",
        "-w", "/work",
        "--entrypoint", "tippecanoe-decode",
        DOCKER_IMAGE,
        "-Z", str(GREEN_ZOOM), "-z", str(GREEN_ZOOM), "-l", "green",
        f"/work/{rel}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError(f"tippecanoe-decode failed with code {proc.returncode}")
    features = []
    for line in proc.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith('{ "type": "Feature"'):
            continue
        feature = json.loads(line)
        props = feature.get("properties") or {}
        if props.get("layer") == "green" and props.get("class") in TREE_DENSITY:
            features.append(feature)
    return features


def _polygon_rings(polygon: Polygon) -> list[list[list[float]]]:
    rings = [[[round(x, 7), round(y, 7)] for x, y in polygon.exterior.coords]]
    rings += [
        [[round(x, 7), round(y, 7)] for x, y in ring.coords]
        for ring in polygon.interiors
    ]
    return rings


def collect_trees(green_features: list[dict], moscow_geom) -> list[dict]:
    """Кроны внутри green (park/wood) в контуре Москвы, без швов районов."""
    prepared = prep(moscow_geom)
    polygons: list[tuple[str, list]] = []
    for feature in green_features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        candidate = shape(geometry)
        if candidate.is_empty or not prepared.intersects(candidate):
            continue
        clipped = candidate.intersection(moscow_geom)
        parts = (
            [clipped]
            if clipped.geom_type == "Polygon"
            else [part for part in getattr(clipped, "geoms", [])
                  if part.geom_type == "Polygon"]
        )
        cls = feature["properties"]["class"]
        for part in parts:
            if not part.is_empty and part.area > 0:
                polygons.append((cls, _polygon_rings(part)))
    trees: list[dict] = []
    for index, (cls, rings) in enumerate(polygons):
        trees.extend(tree_points(cls, rings, index, 0))
    return trees


# --------------------------------------------------------------------------- #
# сборка
# --------------------------------------------------------------------------- #

def write_ndjson(features: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for feature in features:
            fh.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")


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
        "-Z8", "-z14", "-d14", "-r1",
        "--drop-densest-as-needed",
        "-al", "-ap",
        "--maximum-tile-bytes", "350000",
        "--no-tile-stats",
        f"/work/{rel_in}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError(f"tippecanoe failed with code {proc.returncode}")


def vector_layers(path: Path) -> list[str]:
    """Слои из PMTiles-метаданных (gzip JSON) — авторитетная проверка файла."""
    raw = path.read_bytes()
    (_root_off, _root_len, meta_off, meta_len,
     _leaf_off, _leaf_len, _data_off, _data_len) = struct.unpack_from("<8Q", raw, 8)
    try:
        metadata = json.loads(gzip.decompress(raw[meta_off:meta_off + meta_len]))
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError):
        return []
    return [layer.get("id") for layer in metadata.get("vector_layers") or []]


def build(
    out: Path = DEFAULT_OUT,
    *,
    copy_to_web: bool = True,
    basemap: Path = BASEMAP,
    pbf: Path = PBF_PATH,
) -> dict:
    """Собрать overlays.pmtiles и вернуть сводку (фичи, размер, тайлы)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    shop_subtypes = cfg["bonuses"]["categories"]["shops"].get("subtypes", [])

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        moscow_geom = moscow_shape(cur)
        redzones = collect_redzones(cur)
        print(f"  redzones (PostGIS): {len(redzones):,} фич", flush=True)
        poi = collect_poi(cur, shop_subtypes)
        print(f"  poi: {len(poi):,} фич", flush=True)

    pbf_roads, pbf_counts = collect_pbf_roads(pbf, moscow_geom)
    redzones += pbf_roads
    print(
        f"  redzones (PBF): {len(pbf_roads):,} фич {pbf_counts}, "
        f"всего {len(redzones):,}",
        flush=True,
    )

    if not basemap.is_file():
        raise SystemExit(f"нет {basemap}: сначала соберите data/basemap.pmtiles")
    green = decode_green(basemap)
    trees = collect_trees(green, moscow_geom)
    print(
        f"  trees: {len(trees):,} фич из {len(green):,} полигонов green (z{GREEN_ZOOM})",
        flush=True,
    )

    features = redzones + poi + trees
    CITY_DIR.mkdir(parents=True, exist_ok=True)
    staged = CITY_DIR / ".overlays.ndjson"
    try:
        write_ndjson(features, staged)
        print(f"  ndjson: {human_size(staged.stat().st_size)}", flush=True)
        run_tippecanoe(staged, out)
    finally:
        staged.unlink(missing_ok=True)

    stats = pmtiles_stats(out)
    summary = {
        "path": str(out),
        "bytes": out.stat().st_size,
        "features": {"redzones": len(redzones), "poi": len(poi), "trees": len(trees)},
        "pbf_roads": pbf_counts,
        "tiles": stats["tiles"],
        "zooms": (stats["min_zoom"], stats["max_zoom"]),
        "per_zoom": stats["per_zoom"],
    }

    # Контроль слоёв: PMTiles-метаданные перечисляют все vector_layers файла.
    seen = vector_layers(out)
    summary["layers_seen"] = seen
    missing = {"redzones", "poi", "trees"} - set(seen)
    if missing:
        print(f"  ВНИМАНИЕ: в файле нет слоёв {', '.join(sorted(missing))}", flush=True)

    if copy_to_web and WEB_OUT.parent.is_dir():
        WEB_OUT.write_bytes(out.read_bytes())
        summary["web_copy"] = str(WEB_OUT)
    return summary


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-copy", action="store_true",
                        help="не копировать в web/public/data")
    parser.add_argument("--basemap", type=Path, default=BASEMAP)
    parser.add_argument("--pbf", type=Path, default=PBF_PATH,
                        help="PBF для motorway/съездов/всех tertiary")
    args = parser.parse_args(argv)

    summary = build(
        args.out.resolve(),
        copy_to_web=not args.no_copy,
        basemap=args.basemap.resolve(),
        pbf=args.pbf.resolve(),
    )
    features = summary["features"]
    print()
    print(f"overlays.pmtiles: {summary['path']}")
    print(f"  размер   : {human_size(summary['bytes'])} ({summary['bytes']:,} B)")
    print(f"  тайлы    : {summary['tiles']:,} (z{summary['zooms'][0]}–z{summary['zooms'][1]})")
    print(f"  фичи     : redzones {features['redzones']:,}, poi {features['poi']:,}, "
          f"trees {features['trees']:,}")
    print(f"  слои в тайлах: {', '.join(summary['layers_seen']) or '—'}")
    if summary.get("web_copy"):
        print(f"  копия    : {summary['web_copy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
