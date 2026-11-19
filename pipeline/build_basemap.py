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
import sys
import time
import urllib.request
from pathlib import Path

import osmium
from shapely import set_precision
from shapely.geometry import LineString, MultiLineString, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry

# Пресня + буфер: стартовый вид карты (pitch 60) целиком попадает в рамку.
DEFAULT_BBOX = (37.47, 55.72, 37.59, 55.815)
# Захватываем узлы с запасом, чтобы линии на краю рамки не рвались.
NODE_MARGIN = 0.08
PRECISION = 0.00002  # ~2 м — на z16 это меньше пикселя
ROAD_SIMPLIFY = 0.00004  # ~4 м — убирает дрожание GPS-точек OSM
AREA_SIMPLIFY = 0.00005
MIN_AREA = 1e-6  # ~0,7 га: мельче на обзорном масштабе не видно
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
    p.add_argument("--out", type=Path, default=here / "web" / "public" / "data" / "basemap.geojson")
    p.add_argument(
        "--bbox",
        default=",".join(str(v) for v in DEFAULT_BBOX),
        help="min_lon,min_lat,max_lon,max_lat",
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
    min_lon, min_lat, max_lon, max_lat = (float(x) for x in args.bbox.split(","))
    clip_box = box(min_lon, min_lat, max_lon, max_lat)

    if not args.pbf.exists():
        print(f"Нет файла {args.pbf}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)

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

    # Стабильный порядок: земля → зелень → вода → река → дороги.
    order = {"landuse": 0, "green": 1, "water": 2, "waterway": 3, "river": 4, "road": 5}
    ways.features.sort(key=lambda f: order.get(f["properties"]["layer"], 9))

    payload = {"type": "FeatureCollection", "features": ways.features}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    args.out.write_text(text, encoding="utf-8")
    size = args.out.stat().st_size
    print(f"Готово: {args.out} — {size / 1024 / 1024:.2f} МБ", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
