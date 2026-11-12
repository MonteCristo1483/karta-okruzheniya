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
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
VALHALLA = "http://localhost:8002/route"
WORKERS = 8
BATCH = 500

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
WHERE d.osm_id = ANY(%s)
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


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scope_all = "--all" in sys.argv  # эталонные районы (калибровка) вместо Пресни
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
    buildings_sql = BUILDINGS_ALL if scope_all else """
        SELECT osm_id, ST_Centroid(geometry) AS c FROM buildings_presnya
    """
    buildings_params = [district_ids] if scope_all else []
    poi_area_sql = """
        (SELECT ST_Union(ST_Buffer(geometry::geography, 2000)::geometry)
         FROM districts WHERE osm_id = ANY(%s))
    """ if scope_all else "(SELECT geometry FROM presnensky)"
    poi_area_params = [district_ids] if scope_all else []

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

        for category, cat_cfg in categories.items():
            subtypes = cat_cfg.get("subtypes")
            subtype_clause = "AND p.subtype = ANY(%s)" if subtypes else ""
            cur.execute(f"""
                SELECT b.osm_id,
                       ST_Y(ST_Centroid(p.geometry)), ST_X(ST_Centroid(p.geometry))
                FROM tmp_route_buildings b
                CROSS JOIN LATERAL (
                    SELECT p.geometry FROM tmp_route_poi p
                    WHERE p.type = ANY(%s) {subtype_clause}
                    ORDER BY p.geometry <-> b.c
                    LIMIT 1
                ) p
            """, [cat_cfg["poi_types"], *([subtypes] if subtypes else [])])
            nearest = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            for osm_id, center in buildings:
                best = nearest.get(osm_id)
                if best is not None:
                    tasks.append((osm_id, category, center, best))

    print(f"Зданий: {len(buildings):,}, пар здание+категория: {len(tasks):,}")

    done = errors = 0
    buffer: list[tuple] = []
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

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for row in pool.map(worker, tasks, chunksize=32):
                if row is None:
                    errors += 1
                    continue
                buffer.append(row)
                done += 1
                if len(buffer) >= BATCH:
                    cur.executemany(UPSERT, buffer)
                    conn.commit()
                    buffer.clear()
                    print(f"  {done:,}/{len(tasks):,} ({time.time() - t0:.0f} с)")
        if buffer:
            cur.executemany(UPSERT, buffer)
            conn.commit()

    print(f"Готово: {done:,} маршрутов, ошибок: {errors}, {time.time() - t0:.0f} с")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
