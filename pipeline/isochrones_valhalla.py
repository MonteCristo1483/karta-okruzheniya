#!/usr/bin/env python3
"""Изохроны 5/10/15 минут пешком от каждого дома (Valhalla).

Пишет один файл на здание: web/public/data/isochrones/<id>.json

  {"5": <GeoJSON geometry>, "10": ..., "15": ...}

Формат выбран специально: веб грузит только файл выбранного дома (как
routes/), поэтому 3 306 зданий не утяжеляют первую загрузку. Полигоны
упрощены (generalize=50 м, denoise=0.5) — это иллюстрация зоны
пешей доступности, а не точная граница.

Предусловие: valhalla_service на http://localhost:8002
  docker run -d --name valhalla-svc -v $PWD/data/processed/valhalla:/custom_files \
    -p 8002:8002 ghcr.io/valhalla/valhalla:latest valhalla_service /custom_files/valhalla.json

Запуск:
  pipeline/.venv/Scripts/python.exe pipeline/isochrones_valhalla.py
  ... --resume   — не пересчитывать уже готовые файлы
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "data" / "isochrones"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
VALHALLA = "http://localhost:8002/isochrone"
WORKERS = 8
CONTOURS = (5, 10, 15)
ROUND_DECIMALS = 5  # ~1 м, лишние знаки после generalize=50 бесполезны

# Один Session на поток: без keep-alive Windows исчерпывает эфемерные порты
# (WinError 10048), см. decisions.md про маршруты.
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["Content-Type"] = "application/json"
        _local.s.headers["Connection"] = "keep-alive"
    return _local.s


def round_coords(node):
    if isinstance(node, list):
        if node and isinstance(node[0], (int, float)):
            return [round(node[0], ROUND_DECIMALS), round(node[1], ROUND_DECIMALS)]
        return [round_coords(child) for child in node]
    return node


def request_isochrone(lat: float, lon: float) -> dict | None:
    payload = json.dumps(
        {
            "locations": [{"lat": lat, "lon": lon}],
            "costing": "pedestrian",
            "contours": [{"time": minutes} for minutes in CONTOURS],
            "polygons": True,
            "denoise": 0.5,
            "generalize": 50,
        }
    )
    try:
        resp = session().post(VALHALLA, data=payload, timeout=60)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        result: dict = {}
        for feature in features:
            minutes = str(int(round(float(feature["properties"]["contour"]))))
            geometry = feature["geometry"]
            result[minutes] = {
                "type": geometry["type"],
                "coordinates": round_coords(geometry["coordinates"]),
            }
        if set(result) != {str(m) for m in CONTOURS}:
            return None
        return result
    except Exception:
        return None


def main() -> int:
    resume = "--resume" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT osm_id, ST_Y(ST_Centroid(geometry)), ST_X(ST_Centroid(geometry)) "
            "FROM buildings_presnya ORDER BY osm_id"
        )
        buildings = cur.fetchall()

    todo = [
        b for b in buildings
        if not (resume and (OUT_DIR / f"{b[0]}.json").exists())
    ]
    print(f"Зданий: {len(buildings):,}, к расчёту: {len(todo):,}")

    errors: list[int] = []
    done = 0
    total_bytes = 0
    started = time.monotonic()

    def worker(building):
        osm_id, lat, lon = building
        data = request_isochrone(lat, lon)
        if data is None:
            time.sleep(0.5)
            data = request_isochrone(lat, lon)
        if data is None:
            return osm_id, 0
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        (OUT_DIR / f"{osm_id}.json").write_text(payload, encoding="utf-8")
        return osm_id, len(payload.encode("utf-8"))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for osm_id, size in pool.map(worker, todo, chunksize=16):
            done += 1
            if size == 0:
                errors.append(osm_id)
            else:
                total_bytes += size
            if done % 250 == 0:
                print(
                    f"  {done:,}/{len(todo):,}, ошибок {len(errors)}, "
                    f"{time.monotonic() - started:.0f} с"
                )

    print(
        f"Готово: {done - len(errors):,} файлов, ошибок {len(errors)}, "
        f"{total_bytes / 1024 / 1024:.1f} МБ, {time.monotonic() - started:.0f} с"
    )
    if errors:
        print("id с ошибками:", " ".join(map(str, errors[:20])))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
