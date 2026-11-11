#!/usr/bin/env python3
"""Импорт школ с data.mos.ru в poi (type='school', osm_type='mosru').

Формат CSV: двуязычная выгрузка data.mos.ru (первая строка — заголовки EN,
вторая — RU, разделитель ';'). Берём только «общеобразовательная организация».

Координаты: сначала колонка «Центроид» (центроид здания из официальной
выгрузки). Если центроида нет — геокодируем «Адреса учреждения» через
Nominatim: пауза 1.1 с между живыми запросами (лимит сервиса), User-Agent
по требованиям usage policy, результаты кэшируются в
data/processed/geocode_cache.sqlite (не коммитится).

Идемпотентность: upsert по (osm_type='mosru', osm_id=global_id набора).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "schools_mosru.csv"
CACHE_PATH = ROOT / "data" / "processed" / "geocode_cache.sqlite"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

SCHOOL_TYPE = "общеобразовательная организация"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_PAUSE_S = 1.1
USER_AGENT = "karta-okruzheniya/1.0 (schools import; local dev)"

ALTER_TABLE = """
ALTER TABLE poi ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE poi ADD COLUMN IF NOT EXISTS address TEXT
"""

INSERT = """
INSERT INTO poi (osm_type, osm_id, type, geometry, name, address)
VALUES ('mosru', %s, 'school', ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
ON CONFLICT (osm_type, osm_id) DO NOTHING
"""

CENTROID_RE = re.compile(r"coordinates=\[([\d.+-]+),\s*([\d.+-]+)\]")
FULLNAME_RE = re.compile(r"FullName=([^,}\]]+)")


def parse_centroid(cell: str) -> tuple[float, float] | None:
    """«Центроид» вида {type=Point, coordinates=[37.60, 55.70]} -> (lon, lat)."""
    match = CENTROID_RE.search(cell)
    if not match:
        return None
    lon, lat = float(match.group(1)), float(match.group(2))
    if not (55.0 < lat < 57.0 and 36.0 < lon < 40.0):
        return None
    return lon, lat


def parse_address(row: dict) -> str | None:
    match = FULLNAME_RE.search(row.get("Адреса учреждения") or "")
    if match:
        return match.group(1).strip().strip('"')
    legal = (row.get("Юридический адрес") or "").strip().strip('"')
    return legal or None


class Geocoder:
    def __init__(self, cache_path: Path) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS geocode "
            "(query TEXT PRIMARY KEY, lat REAL, lon REAL, ok INTEGER)"
        )
        self.db.commit()

    def locate(self, query: str) -> tuple[float, float] | None:
        cached = self.db.execute(
            "SELECT lat, lon, ok FROM geocode WHERE query = ?", (query,)
        ).fetchone()
        if cached is not None:
            lat, lon, ok = cached
            return (lon, lat) if ok else None
        lon, lat, ok = self._request(query)
        self.db.execute(
            "INSERT OR REPLACE INTO geocode VALUES (?, ?, ?, ?)",
            (query, lat, lon, ok),
        )
        self.db.commit()
        time.sleep(GEOCODE_PAUSE_S)
        return (lon, lat) if ok else None

    def _request(self, query: str) -> tuple[float, float, int]:
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "ru"}
        )
        request = urllib.request.Request(
            f"{GEOCODE_URL}?{params}", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                results = json.loads(response.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None, None, 0
        if not results:
            return None, None, 0
        try:
            return float(results[0]["lon"]), float(results[0]["lat"]), 1
        except (KeyError, ValueError, IndexError):
            return None, None, 0


def read_schools() -> list[dict]:
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header = rows[1]
    schools = []
    for cells in rows[2:]:
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        if row.get("Тип образовательной организации", "").strip() != SCHOOL_TYPE:
            continue
        schools.append(row)
    return schools


def main() -> int:
    schools = read_schools()
    geocoder = Geocoder(CACHE_PATH)
    stats = {"total": len(schools), "inserted": 0, "geocoded": 0, "no_coords": 0}

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(ALTER_TABLE)
        for i, row in enumerate(schools, 1):
            global_id = int(row["global_id"])
            name = (row.get("Сокращённое наименование") or "").strip().strip('"') or None
            address = parse_address(row)
            coords = parse_centroid(row.get("Центроид") or "")
            if coords is None:
                if not address:
                    stats["no_coords"] += 1
                    continue
                coords = geocoder.locate(address)
                if coords is None:
                    stats["no_coords"] += 1
                    continue
                stats["geocoded"] += 1
            cur.execute(INSERT, (global_id, coords[0], coords[1], name, address))
            stats["inserted"] += cur.rowcount
            if i % 50 == 0:
                print(f"\rОбработано {i}/{len(schools)}", end="", flush=True)
        conn.commit()
    print()
    print(f"Итого: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
