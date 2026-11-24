"""Подложка Пресни из OSM: компактный GeoJSON (улицы, вода, зелень, землепользование).

Зачем: полноценная подложка без внешних тайл-серверов и без платных API.
Мы берём только нужные слои OSM (без зданий, POI, адресов и мультиязычных
названий), поэтому файл в разы легче готовых тайлов и полностью работает
офлайн (service worker кэширует data/*).

Использование:
    pipeline/.venv/Scripts/python.exe pipeline/build_basemap.py \
        --pbf data/raw/moscow.osm.pbf \
        --out web/public/data/basemap.geojson

Windows: pyosmium не открывает PBF по пути с кириллицей. Если репозиторий
лежит в «Документы», передайте --pbf на ASCII-путь (жёсткая ссылка:
`cmd /c mklink /H C:\\temp\\moscow.osm.pbf <путь>`) либо запускайте из
ASCII-папки.

Формат: одна FeatureCollection, у каждой фичи layer:
    landuse  — residential/commercial/industrial (фон кварталов)
    green    — парки, скверы, лес, луг
    water    — полигоны воды (пруды, озёра)
    river    — осевая линия Москвы-реки (рисуется широкой лентой)
    waterway — линии рек/ручьёв/каналов
    road     — улицы: major / minor

Подписи улиц рисует веб-карта из свойств name (DOM-подписи, без глифов),
поэтому шрифтовые PBF и внешние глиф-серверы не нужны.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import osmium
from shapely import set_precision
from shapely.geometry import (
    LineString,
    MultiLineString,
    Polygon,
    box,
    mapping,
    shape as shapely_shape,
)
from shapely.geometry.base import BaseGeometry

# Пресня + буфер: стартовый вид карты (pitch 60) целиком попадает в рамку.
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BBOX = (37.47, 55.72, 37.59, 55.815)
# Захватываем узлы с запасом, чтобы линии на краю рамки не рвались.
NODE_MARGIN = 0.08
PRECISION = 0.00002  # ~2 м — на z16 это меньше пикселя
ROAD_SIMPLIFY = 0.00004  # ~4 м — убирает дрожание GPS-точек OSM
AREA_SIMPLIFY = 0.00005
MIN_AREA = 1e-6  # ~0,7 га: мельче на обзорном масштабе не видно
DOCKER_IMAGE = "karta-tippecanoe:2.79.0"
LAYER_ORDER = {"landuse": 0, "green": 1, "water": 2, "waterway": 3, "river": 4, "road": 5}
ROAD_CLASSES = {
    "motorway": "major",
    "motorway_link": "major",
    "trunk": "major",
    "trunk_link": "major",
    "primary": "major",
    "primary_link": "major",
    "secondary": "major",
    "secondary_link": "major",
    "tertiary": "major",
    "tertiary_link": "major",
    "residential": "minor",
    "unclassified": "minor",
    "living_street": "minor",
    "road": "minor",
    "pedestrian": "minor",
}

WATERWAY_KINDS = {"river", "stream", "canal", "drain", "ditch"}
GREEN_LEISURE = {"park", "garden", "playground", "nature_reserve", "common"}
GREEN_LANDUSE = {"grass", "forest", "meadow", "recreation_ground", "village_green"}
GREEN_NATURAL = {"wood", "scrub", "grassland", "heath"}
LANDUSE_KINDS = {"residential", "commercial", "industrial", "retail"}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pbf", type=Path, default=here / "data" / "raw" / "moscow.osm.pbf")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="geojson для режима одной рамки "
        "(по умолчанию web/public/data/basemap.geojson)",
    )
    p.add_argument(
        "--bbox",
        default=None,
        help="min_lon,min_lat,max_lon,max_lat; без флага — Пресня "
        "или bbox всех районов из data/districts при --pmtiles",
    )
    p.add_argument(
        "--pmtiles",
        action="store_true",
        help="собрать PMTiles для bbox районов из data/districts/<id>/district.geojson",
    )
    p.add_argument(
        "--districts-dir",
        type=Path,
        default=here / "data" / "districts",
        help="каталог районов для --pmtiles",
    )
    p.add_argument(
        "--river-json",
        type=Path,
        default=here / "data" / "raw" / "moskva-river-389341.json",
        help="полный JSON отношения Москвы-реки (OSM API /relation/389341/full.json)",
    )
    return p.parse_args()


class NodeCollector(osmium.SimpleHandler):
    """Запоминаем координаты узлов внутри рамки с запасом."""

    def __init__(self, bounds: tuple[float, float, float, float]):
        super().__init__()
        self.min_lon, self.min_lat, self.max_lon, self.max_lat = bounds
        self.coords: dict[int, tuple[float, float]] = {}

    def node(self, n: osmium.osm.Node) -> None:  # type: ignore[name-defined]
        loc = n.location
        if not loc.valid():
            return
        lon, lat = loc.lon, loc.lat
        if self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat:
            self.coords[n.id] = (lon, lat)


def name_of(tags: dict[str, str]) -> str | None:
    name = tags.get("name:ru") or tags.get("name")
    if not name:
        return None
    name = " ".join(name.split())
    return name or None


def classify(tags: dict[str, str]) -> tuple[str, str, str | None] | None:
    """Возвращает (layer, class, name) или None, если объект не нужен."""
    name = name_of(tags)
    highway = tags.get("highway")
    if highway in ROAD_CLASSES:
        return ("road", ROAD_CLASSES[highway], name)
    waterway = tags.get("waterway")
    if waterway in WATERWAY_KINDS:
        # riverbank — это полигон, его обработаем ниже.
        if waterway == "riverbank":
            return ("water", "water", name)
        return ("waterway", "waterway", name)
    if tags.get("natural") == "water" or tags.get("landuse") == "reservoir":
        return ("water", "water", name)
    leisure = tags.get("leisure")
    if leisure in GREEN_LEISURE:
        return ("green", "park", name)
    landuse = tags.get("landuse")
    if landuse in GREEN_LANDUSE:
        return ("green", "wood" if landuse == "forest" else "park", name)
    if tags.get("natural") in GREEN_NATURAL:
        return ("green", "wood", name)
    if landuse in LANDUSE_KINDS:
        return ("landuse", landuse, name)
    return None


class WayCollector(osmium.SimpleHandler):
    def __init__(
        self,
        coords: dict[int, tuple[float, float]],
        clip_box: BaseGeometry,
    ):
        super().__init__()
        self.coords = coords
        self.clip_box = clip_box
        self.features: list[dict] = []
        self.stats: dict[str, int] = {}

    def way(self, w: osmium.osm.Way) -> None:  # type: ignore[name-defined]
        tags = {t.k: t.v for t in w.tags}
        kind = classify(tags)
        if kind is None:
            return
        layer, cls, name = kind
        pts: list[tuple[float, float]] = []
        missing = 0
        for n in w.nodes:
            c = self.coords.get(n.ref)
            if c is None:
                missing += 1
            else:
                pts.append(c)
        if len(pts) < 2:
            return
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        is_area = layer in {"water", "green", "landuse"} and closed
        if layer in {"water", "green", "landuse"} and not is_area:
            return
        if missing:
            # Геометрия на краю буфера может быть неполной — пропускаем,
            # чтобы не рисовать ложные обрывы. Внутри рамки таких мало.
            return
        coords = [list(p) for p in pts]
        try:
            geom = Polygon(coords) if is_area else LineString(coords)
            if not geom.is_valid:
                geom = geom.buffer(0)
            geom = geom.intersection(self.clip_box)
            if geom.is_empty:
                return
            geom = geom.simplify(AREA_SIMPLIFY if is_area else ROAD_SIMPLIFY, preserve_topology=is_area)
            if is_area and geom.area < MIN_AREA:
                return
            geom = set_precision(geom, PRECISION)
            if geom.is_empty:
                return
        except Exception:
            return
        props = {"layer": layer, "class": cls}
        if name:
            props["name"] = name
        for part in _parts(geom):
            self.features.append(
                {"type": "Feature", "properties": props, "geometry": mapping(part)}
            )
        key = f"{layer}/{cls}"
        self.stats[key] = self.stats.get(key, 0) + 1


def _parts(geom: BaseGeometry) -> list[BaseGeometry]:
    if geom.geom_type in {"Polygon", "LineString"}:
        return [geom]
    if geom.geom_type == "GeometryCollection":
        out: list[BaseGeometry] = []
        for g in geom.geoms:  # type: ignore[attr-defined]
            out.extend(_parts(g))
        return out
    if hasattr(geom, "geoms"):  # MultiPolygon / MultiLineString
        return [g for g in geom.geoms if not g.is_empty]  # type: ignore[attr-defined]
    return []


def river_features(path: Path, clip_box: BaseGeometry) -> list[dict]:
    """Осевая линия Москвы-реки из отношения OSM 389341.

    В OSM река — отношение waterway=river (линия), а не полигон, поэтому
    на карте рисуем её широкой лентой. Файл кэшируется в data/raw; если
    его нет — скачиваем один раз с OSM API.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        url = "https://api.openstreetmap.org/api/0.6/relation/389341/full.json"
        print(f"  река: скачиваем {url}", flush=True)
        with urllib.request.urlopen(url) as r, path.open("wb") as f:
            f.write(r.read())
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, list[int]] = {}
    relation: dict | None = None
    for e in data.get("elements", []):
        if e.get("type") == "node":
            nodes[e["id"]] = (e["lon"], e["lat"])
        elif e.get("type") == "way":
            ways[e["id"]] = e.get("nodes", [])
        elif e.get("type") == "relation" and relation is None:
            relation = e
    if relation is None:
        return []
    lines: list[list[tuple[float, float]]] = []
    for m in relation.get("members", []):
        if m.get("type") != "way":
            continue
        pts = [nodes[n] for n in ways.get(m["ref"], []) if n in nodes]
        if len(pts) >= 2:
            lines.append(pts)
    if not lines:
        return []
    geom = MultiLineString(lines).intersection(clip_box)
    if geom.is_empty:
        return []
    geom = geom.simplify(ROAD_SIMPLIFY, preserve_topology=False)
    geom = set_precision(geom, PRECISION)
    name = (relation.get("tags") or {}).get("name:ru") or (relation.get("tags") or {}).get("name")
    out: list[dict] = []
    for part in _parts(geom):
        out.append(
            {
                "type": "Feature",
                "properties": {"layer": "river", "class": "river", "name": name or "Москва"},
                "geometry": mapping(part),
            }
        )
    return out


def main() -> int:
    args = parse_args()
    if args.pmtiles:
        return build_pmtiles(args)

    min_lon, min_lat, max_lon, max_lat = (
        float(x) for x in (args.bbox or ",".join(str(v) for v in DEFAULT_BBOX)).split(",")
    )
    clip_box = box(min_lon, min_lat, max_lon, max_lat)

    out = args.out or (ROOT / "web" / "public" / "data" / "basemap.geojson")
    if not args.pbf.exists():
        print(f"Нет файла {args.pbf}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Проход 1/2: узлы в рамке {args.bbox} (+{NODE_MARGIN})…", flush=True)
    t0 = time.time()
    nodes = NodeCollector(
        (min_lon - NODE_MARGIN, min_lat - NODE_MARGIN, max_lon + NODE_MARGIN, max_lat + NODE_MARGIN)
    )
    nodes.apply_file(str(args.pbf))
    print(f"  узлов: {len(nodes.coords):,} за {time.time() - t0:.0f} с", flush=True)

    print("Проход 2/2: улицы, вода, зелень…", flush=True)
    t0 = time.time()
    ways = WayCollector(nodes.coords, clip_box)
    ways.apply_file(str(args.pbf))
    print(f"  объектов: {len(ways.features):,} за {time.time() - t0:.0f} с", flush=True)
    print("  по слоям:", ", ".join(f"{k}={v:,}" for k, v in sorted(ways.stats.items())), flush=True)

    print("Река: осевая линия Москвы-реки…", flush=True)
    rivers = river_features(args.river_json, clip_box)
    print(f"  линий реки: {len(rivers)}", flush=True)
    ways.features.extend(rivers)
    ways.features.sort(key=lambda f: LAYER_ORDER.get(f["properties"]["layer"], 9))

    write_geojson(out, ways.features)
    size = out.stat().st_size
    print(f"Готово: {out} — {size / 1024 / 1024:.2f} МБ", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# PMTiles по районам (V2-S2a)
# --------------------------------------------------------------------------- #

def _walk_coords(node, xs: list[float], ys: list[float]) -> None:
    if node and isinstance(node[0], (int, float)):
        xs.append(float(node[0]))
        ys.append(float(node[1]))
        return
    for child in node:
        _walk_coords(child, xs, ys)


def geometry_bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    _walk_coords(geometry.get("coordinates") or [], xs, ys)
    if not xs:
        raise ValueError("empty geometry")
    return min(xs), min(ys), max(xs), max(ys)


def district_boxes(directory: Path) -> list[dict]:
    boxes = []
    for district_dir in sorted(directory.iterdir()):
        path = district_dir / "district.geojson"
        if not district_dir.is_dir() or not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        feature = payload["features"][0]
        bbox = geometry_bbox(feature["geometry"])
        name = (feature.get("properties") or {}).get("name") or district_dir.name
        summary = district_dir / "summary.json"
        if summary.exists():
            name = json.loads(summary.read_text(encoding="utf-8"))["district"]["name"]
        boxes.append(
            {"id": district_dir.name, "name": name, "bbox": bbox, "dir": district_dir}
        )
    return boxes


def write_geojson(path: Path, features: list[dict]) -> None:
    text = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    path.write_text(text, encoding="utf-8")


def write_basemap_ndjson(features: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for feature in features:
            props = feature["properties"]
            line = {
                "type": "Feature",
                "tippecanoe": {"layer": props["layer"]},
                "properties": props,
                "geometry": feature["geometry"],
            }
            fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")


def run_tippecanoe_basemap(ndjson: Path, out: Path) -> None:
    rel_in = ndjson.relative_to(ROOT).as_posix()
    rel_out = out.relative_to(ROOT).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ROOT}:/work",
        "-w", "/work",
        DOCKER_IMAGE,
        "--force",
        "-o", f"/work/{rel_out}",
        "-Z6", "-z16", "-d13",
        # LOD-A: прореживание в tippecanoe не трогает линии/полигоны без
        # -al/-ap, а --drop-densest-as-needed на плотном центре не дотягивает
        # до лимита. -an (--drop-smallest-as-needed) режет самые мелкие
        # объекты только в тайлах, которые реально больше лимита; -r1 —
        # чтобы не включилось фоновое прореживание по -r и улицы не редели
        # на z13-z16.
        "--drop-smallest-as-needed", "-al", "-ap", "-r1",
        "--maximum-tile-bytes", "300000",
        "--no-tile-stats",
        f"/work/{rel_in}",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError(f"tippecanoe failed with code {proc.returncode}")


def clip_features(features: list[dict], clip_box: BaseGeometry) -> list[dict]:
    clipped = []
    for feature in features:
        try:
            geom = shapely_shape(feature["geometry"]).intersection(clip_box)
            if geom.is_empty:
                continue
            geom = set_precision(geom, PRECISION)
        except Exception:
            continue
        props = dict(feature["properties"])
        for part in _parts(geom):
            clipped.append(
                {"type": "Feature", "properties": props, "geometry": mapping(part)}
            )
    return clipped


def human(size: int) -> str:
    return f"{size / 1024:.0f} KiB" if size < 1 << 20 else f"{size / (1 << 20):.2f} MiB"


def build_pmtiles(args: argparse.Namespace) -> int:
    boxes = district_boxes(args.districts_dir.resolve())
    if not boxes:
        raise SystemExit(f"Нет районов в {args.districts_dir}")
    union = (
        min(item["bbox"][0] for item in boxes),
        min(item["bbox"][1] for item in boxes),
        max(item["bbox"][2] for item in boxes),
        max(item["bbox"][3] for item in boxes),
    )
    min_lon, min_lat, max_lon, max_lat = union
    clip_box = box(min_lon, min_lat, max_lon, max_lat)
    if not args.pbf.exists():
        print(f"Нет файла {args.pbf}", file=sys.stderr)
        return 1

    print(f"PMTiles для {len(boxes)} районов, общая рамка "
          f"{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}", flush=True)
    print(f"Проход 1/2: узлы (+{NODE_MARGIN})…", flush=True)
    t0 = time.time()
    nodes = NodeCollector(
        (min_lon - NODE_MARGIN, min_lat - NODE_MARGIN,
         max_lon + NODE_MARGIN, max_lat + NODE_MARGIN)
    )
    nodes.apply_file(str(args.pbf))
    print(f"  узлов: {len(nodes.coords):,} за {time.time() - t0:.0f} с", flush=True)

    print("Проход 2/2: улицы, вода, зелень…", flush=True)
    t0 = time.time()
    ways = WayCollector(nodes.coords, clip_box)
    ways.apply_file(str(args.pbf))
    print(f"  объектов: {len(ways.features):,} за {time.time() - t0:.0f} с", flush=True)

    print("Река: осевая линия Москвы-реки…", flush=True)
    try:
        rivers = river_features(args.river_json, clip_box)
    except Exception as exc:  # сеть недоступна — подложка без реки, не блокируем
        print(f"  предупреждение: река не загружена ({exc})", flush=True)
        rivers = []
    print(f"  линий реки: {len(rivers)}", flush=True)
    ways.features.extend(rivers)

    total_geojson = total_pmtiles = 0
    rows = []
    for item in boxes:
        bbox = item["bbox"]
        features = clip_features(ways.features, box(*bbox))
        features.sort(key=lambda f: LAYER_ORDER.get(f["properties"]["layer"], 9))
        out_dir = item["dir"]
        geojson_path = out_dir / "basemap.geojson"
        pmtiles_path = out_dir / "basemap.pmtiles"
        write_geojson(geojson_path, features)
        staged = out_dir / ".basemap.ndjson"
        try:
            write_basemap_ndjson(features, staged)
            run_tippecanoe_basemap(staged, pmtiles_path)
        finally:
            staged.unlink(missing_ok=True)
        gj = geojson_path.stat().st_size
        pm = pmtiles_path.stat().st_size
        total_geojson += gj
        total_pmtiles += pm
        rows.append((item["id"], item["name"], len(features), gj, pm))
        print(
            f"  {item['id']} {item['name']}: {len(features):,} объектов, "
            f"geojson {human(gj)}, pmtiles {human(pm)} "
            f"({pm / gj * 100:.0f}%)",
            flush=True,
        )

    print(f"Итого: geojson {human(total_geojson)}, pmtiles {human(total_pmtiles)} "
          f"({total_pmtiles / max(1, total_geojson) * 100:.0f}%)")
    avg = total_pmtiles / len(rows)
    print(f"Экстраполяция на 125 районов: {human(int(avg * 125))} (среднее x 125)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
