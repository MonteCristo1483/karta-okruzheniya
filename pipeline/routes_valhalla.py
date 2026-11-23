#!/usr/bin/env python3
"""Пешие маршруты здание → ближайший POI каждой категории (V2, Valhalla).

Для каждого здания buildings_presnya и каждой категории бонусов из
config/scoring.json находит ближайший POI (по прямой — та же логика, что
в скоринге и на фронте), просит пешеходный маршрут у локального Valhalla
и сохраняет результат в таблицу routes.

Предусловие: valhalla_service запущен на http://localhost:8002
  docker run -d --name valhalla-svc -v $PWD/data/processed/valhalla:/custom_files \
    -p 8002:8002 ghcr.io/valhalla/valhalla:latest valhalla_service /custom_files/valhalla.json

Запуск:
  pipeline/.venv/Scripts/python.exe pipeline/routes_valhalla.py
  ... --district 1257786   — только жилые района; батч резюмируемый:
                             готовые пары (дом, категория) не пересчитываются,
                             а по завершении пишутся routes/<id>.json для веба.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "scoring.json"
ROUTES_DIR = ROOT / "web" / "public" / "data" / "routes"
from db import DB_DSN
VALHALLA = "http://localhost:8002/route"
WORKERS = 8
BATCH = 500
# Для каждой категории рассматриваем несколько ближайших POI и оставляем
# самый быстрый пеший маршрут: «ближайший по прямой» может оказаться за
# путями/рекой и дать обход в километры (найдено 19.11.2026).
CANDIDATES = 3

# Пул keep-alive соединений: один Session на поток (Session не потокобезопасен).
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["Content-Type"] = "application/json"
        _local.s.headers["Connection"] = "keep-alive"
    return _local.s

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS routes (
    building_id BIGINT NOT NULL,
    category    TEXT NOT NULL,
    distance_m  INT NOT NULL,
    minutes     NUMERIC(6, 1) NOT NULL,
    geometry    GEOMETRY(LineString, 4326) NOT NULL,
    PRIMARY KEY (building_id, category)
)
"""

UPSERT = """
INSERT INTO routes (building_id, category, distance_m, minutes, geometry)
VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
ON CONFLICT (building_id, category) DO UPDATE
SET distance_m = EXCLUDED.distance_m,
    minutes    = EXCLUDED.minutes,
    geometry   = EXCLUDED.geometry
"""

BUILDINGS = """
SELECT b.osm_id, ST_Y(ST_Centroid(b.geometry)), ST_X(ST_Centroid(b.geometry))
FROM buildings_presnya b
WHERE b.is_home
"""

# Для калибровки (флаг --all): здания в эталонных районах.
# POI берём с буфером 2 км — иначе у границы района ближайший вход в метро
# «потерялся бы» за пределами полигона (соседний район).
# Скоуп — ПЕРЕСЕЧЕНИЕ с эталонными районами: иначе в выборку попадает
# Новая Москва (Троицк, Щербинка...) — 1.38 млн «сараев», расчёт
# растягивается на часы и портит медианы (находка 12.11).
# Пресненский в скоуп не входит: его маршруты считаются в обычном режиме.
BUILDINGS_ALL = """
SELECT DISTINCT b.osm_id, ST_Centroid(b.geometry) AS c
FROM buildings b
JOIN districts d ON ST_Intersects(b.geometry, d.geometry)
WHERE d.osm_id = ANY(%s) AND b.is_home
"""

POI_ALL = """
SELECT DISTINCT p.osm_id, p.type, p.subtype,
       ST_Y(ST_Centroid(p.geometry)), ST_X(ST_Centroid(p.geometry))
FROM poi p
JOIN districts d ON ST_Intersects(p.geometry, ST_Buffer(d.geometry::geography, 2000)::geometry)
"""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def decode_polyline(encoded: str, precision: int = 6) -> list[list[float]]:
    """Декодер encoded polyline (Valhalla shape, precision 6)."""
    coords: list[list[float]] = []
    index = 0
    lat = lon = 0
    factor = 10 ** precision
    while index < len(encoded):
        result = shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat

        result = shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlon = ~(result >> 1) if result & 1 else result >> 1
        lon += dlon
        coords.append([lon / factor, lat / factor])
    return coords


def request_route(a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    """Пешеходный маршрут между точками (lat, lon). Одна попытка."""
    payload = json.dumps(
        {
            "locations": [
                {"lat": a[0], "lon": a[1]},
                {"lat": b[0], "lon": b[1]},
            ],
            "costing": "pedestrian",
            "units": "kilometers",
        }
    )
    try:
        # keep-alive через Session: без пула соединений Windows исчерпывает
        # эфемерные порты (WinError 10048) и ~6% запросов падает.
        resp = session().post(VALHALLA, data=payload, timeout=30)
        resp.raise_for_status()
        trip = resp.json()["trip"]
        leg = trip["legs"][0]
        shape = decode_polyline(leg["shape"])
        return {
            "distance_m": round(leg["summary"]["length"] * 1000),
            "minutes": round(leg["summary"]["time"] / 60, 1),
            "shape": shape,
        }
    except Exception:
        return None


def export_routes_files(cur, building_ids: list[int], out_dir: Path) -> tuple[int, int]:
    """routes/<id>.json для веба — формат export_map_data (категории + breakdown)."""
    per_building: dict[int, dict] = {}
    cur.execute(
        """
        SELECT r.building_id, r.category, r.minutes, r.distance_m, ST_AsGeoJSON(r.geometry)
        FROM routes r
        WHERE r.building_id = ANY(%s)
        ORDER BY r.building_id, r.category
        """,
        (building_ids,),
    )
    for building_id, category, minutes, distance_m, geom_json in cur.fetchall():
        entry = per_building.setdefault(building_id, {"categories": {}})
        entry["categories"][category] = {
            "minutes": float(minutes),
            "distance_m": distance_m,
            "geometry": json.loads(geom_json),
        }
    cur.execute(
        "SELECT building_id, breakdown FROM scores WHERE building_id = ANY(%s)",
        (building_ids,),
    )
    breakdowns = dict(cur.fetchall())

    out_dir.mkdir(parents=True, exist_ok=True)
    written = unchanged = 0
    for building_id, entry in per_building.items():
        breakdown = breakdowns.get(building_id)
        if breakdown is not None:
            entry["breakdown"] = breakdown
        text = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        path = out_dir / f"{building_id}.json"
        # Файл с тем же содержимым (например, у дома на границе с Пресней)
        # не перезаписываем: порядок ключей для веба не важен.
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) == entry:
            unchanged += 1
            continue
        path.write_text(text, encoding="utf-8")
        written += 1
    return written, unchanged


def main() -> int:
    argv = sys.argv[1:]
    district_id: int | None = None
    if "--district" in argv:
        index = argv.index("--district")
        if index + 1 >= len(argv):
            print("--district требует osm_id района", file=sys.stderr)
            return 2
        district_id = int(argv[index + 1])
        del argv[index:index + 2]
    args = [a for a in argv if not a.startswith("--")]
    scope_all = "--all" in argv  # эталонные районы (калибровка) вместо Пресни
    if scope_all and district_id is not None:
        print("--all и --district взаимоисключающие", file=sys.stderr)
        return 2
    only = set(args)  # например: routes_valhalla.py transport — только эти категории
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    categories = {
        k: v for k, v in cfg["bonuses"]["categories"].items() if not only or k in only
    }
    if not categories:
        print("Не осталось категорий после фильтрации --only", file=sys.stderr)
        return 2

    # Скоуп по эталонному набору (data/reference_set.md → districts.osm_id):
    # только 30 районов сверки + Пресненский (см. BUILDINGS_ALL).
    if scope_all:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import reference_check
        with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
            refs = reference_check.match_districts(
                cur, reference_check.parse_reference()
            )
        district_ids = [r["osm_id"] for r in refs]
        print(f"Эталонных районов: {len(district_ids)}")
    else:
        district_ids = []

    # Ближайший POI ищем KNN-запросом по временным таблицам с индексами.
    # Для Пресни POI берём с буфером 3 км: иначе у домов на границе района
    # ближайший вход в метро/школа за границей отбрасывается и маршрут
    # уходит в обход через весь район (найдено 19.11.2026: 2.6 км вместо
    # 150 м у Тверского бульвара и Красной Пресни).
    if district_id is not None:
        # Любой район: жилые дома района и POI в буфере 3 км (как в Пресне),
        # чтобы вход в метро за границей района не терялся.
        buildings_sql = (
            "SELECT b.osm_id, ST_Centroid(b.geometry) AS c "
            "FROM district_buildings(%s) b WHERE b.is_home"
        )
        buildings_params = [district_id]
        poi_area_sql = (
            "(SELECT ST_Buffer(geometry::geography, 3000)::geometry "
            "FROM districts WHERE osm_id = %s)"
        )
        poi_area_params = [district_id]
    elif scope_all:
        buildings_sql = BUILDINGS_ALL
        buildings_params = [district_ids]
        poi_area_sql = """
        (SELECT ST_Union(ST_Buffer(geometry::geography, 2000)::geometry)
         FROM districts WHERE osm_id = ANY(%s))
    """
        poi_area_params = [district_ids]
    else:
        buildings_sql = """
        SELECT osm_id, ST_Centroid(geometry) AS c FROM buildings_presnya WHERE is_home
    """
        buildings_params = []
        poi_area_sql = (
            "(SELECT ST_Buffer(geometry::geography, 3000)::geometry FROM presnensky)"
        )
        poi_area_params = []

    tasks: list[tuple[int, str, tuple[float, float], tuple[float, float]]] = []
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        # Материализуем здания и POI во временные таблицы с индексами:
        # KNN по исходным таблицам с фильтром-union из подзапроса
        # планировщиком выполнялся минутами (pg_stat_activity 12.11).
        cur.execute("DROP TABLE IF EXISTS tmp_route_buildings")
        cur.execute(f"""
            CREATE TEMP TABLE tmp_route_buildings AS
            SELECT b.osm_id, b.c
            FROM ({buildings_sql}) b
        """, buildings_params)
        cur.execute("CREATE INDEX ON tmp_route_buildings USING gist (c)")
        cur.execute(f"""
            CREATE TEMP TABLE tmp_route_poi AS
            SELECT p.osm_id, p.type, p.subtype, p.geometry
            FROM poi p
            WHERE ST_Intersects(p.geometry, {poi_area_sql})
        """, poi_area_params)
        cur.execute("CREATE INDEX ON tmp_route_poi USING gist (geometry)")
        cur.execute("ANALYZE tmp_route_buildings")
        cur.execute("ANALYZE tmp_route_poi")
        conn.commit()

        cur.execute("SELECT osm_id, ST_Y(c), ST_X(c) FROM tmp_route_buildings")
        buildings = [(r[0], (r[1], r[2])) for r in cur.fetchall()]
        centers = dict(buildings)

        # Резюмируемость: пары (дом, категория), уже лежащие в routes,
        # не пересчитываем — иначе повторный запуск после обрыва снова
        # считал бы всё с нуля.
        done: set[tuple[int, str]] = set()
        if district_id is not None and buildings:
            cur.execute(
                "SELECT building_id, category FROM routes WHERE building_id = ANY(%s)",
                ([b[0] for b in buildings],),
            )
            done = {(row[0], row[1]) for row in cur.fetchall()}

        skipped = 0
        for category, cat_cfg in categories.items():
            subtypes = cat_cfg.get("subtypes")
            subtype_clause = "AND p.subtype = ANY(%s)" if subtypes else ""
            cur.execute(f"""
                SELECT b.osm_id, p.osm_id,
                       ST_Y(ST_Centroid(p.geometry)), ST_X(ST_Centroid(p.geometry))
                FROM tmp_route_buildings b
                CROSS JOIN LATERAL (
                    SELECT p.osm_id, p.geometry FROM tmp_route_poi p
                    WHERE p.type = ANY(%s) {subtype_clause}
                    ORDER BY p.geometry <-> b.c
                    LIMIT %s
                ) p
            """, [cat_cfg["poi_types"], *([subtypes] if subtypes else []), CANDIDATES])
            for osm_id, _poi_id, plat, plon in cur.fetchall():
                if (osm_id, category) in done:
                    skipped += 1
                    continue
                tasks.append((osm_id, category, centers[osm_id], (plat, plon)))

    print(f"Зданий: {len(buildings):,}, кандидатов здание+категория: {len(tasks):,}")
    if skipped:
        print(f"  уже готово (пропущено кандидатов): {skipped:,}")

    errors = 0
    best: dict[tuple[int, str], tuple] = {}
    t0 = time.time()

    def worker(task):
        osm_id, category, a, b = task
        # Повторная попытка: при параллельной нагрузке Valhalla периодически
        # роняет отдельные запросы (локальный прогон 12.11: ~6% ошибок,
        # все исчезли при повторе).
        result = request_route(a, b)
        if result is None:
            time.sleep(0.5)
            result = request_route(a, b)
        if result is None:
            return None
        line = f"LINESTRING({', '.join(f'{lon} {lat}' for lon, lat in result['shape'])})"
        return (osm_id, category, result["distance_m"], result["minutes"], line)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for done, row in enumerate(pool.map(worker, tasks, chunksize=32), 1):
            if row is None:
                errors += 1
            else:
                key = (row[0], row[1])
                current = best.get(key)
                if current is None or row[3] < current[3]:
                    best[key] = row
            if done % 2000 == 0:
                print(f"  {done:,}/{len(tasks):,} ({time.time() - t0:.0f} с)")

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        if best:
            cur.executemany(UPSERT, list(best.values()))
            conn.commit()
        if district_id is not None:
            written, unchanged = export_routes_files(
                cur, [b[0] for b in buildings], ROUTES_DIR
            )
            print(
                f"routes/<id>.json: записано {written:,}, без изменений {unchanged:,}"
            )

    print(
        f"Готово: {len(best):,} маршрутов (лучших из кандидатов), "
        f"ошибок: {errors}, {time.time() - t0:.0f} с"
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
