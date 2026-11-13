#!/usr/bin/env python3
"""M8: год постройки зданий — ГИС ЖКХ (dom.gosuslugi.ru) + адресный реестр data.mos.ru.

Источники (публичные API, без ключей; проверено живыми запросами 13.11.2026):
  1. Адресный реестр data.mos.ru (набор 60562, data/raw/mos_areestr_full.zip):
     фильтр по DISTRICT (подстрока) и типу объекта «здание» ->
     UNOM, N_FIAS (house-GUID ФИАС), Centroid (lon/lat).
  2. Пакетный обмен FIAS -> внутренний GUID дома ГИС ЖКХ:
     GET /homemanagement/.../houses/searchByFiasHouseCodeList/{fias1,fias2,...}
     (больше ~200 кодов в URI -> HTTP 414; берём пачки по 100).
  3. Паспорт дома: GET /homemanagement/.../houses/public/{typeCode}/{guid}
     -> buildingYear, operationYear, planSeries, intWallMaterialList.
     Покрывает только дома с паспортами (МКД/частные); офиски и прочие
     нежилые здания в ГИС ЖКХ отсутствуют — для Пресни это ~40% реестра.
  4. PostGIS: таблица building_years (точки реестра + год) и spatial join
     к buildings_presnya -> building_year_match (osm_id, year, distance_m),
     ближайший объект в пределах 50 м.

Фазы (по одной или все разом): registry -> exchange -> passports -> load.
Кэш паспортов: data/processed/build_year_cache.ndjson — прерывание
и повторный запуск passports докачивают только недостающее.

Требования: PostGIS из docker compose (как у остальных импортов).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import zipfile
import csv
import io
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_ZIP = ROOT / "data" / "raw" / "mos_areestr_full.zip"
PROC = ROOT / "data" / "processed"
REGISTRY_JSON = PROC / "presnya_registry.json"
HOUSES_JSON = PROC / "presnya_houses.json"
PASSPORT_CACHE = PROC / "build_year_cache.ndjson"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"

GIS_ZHKH = "https://dom.gosuslugi.ru"
BATCH_URL = (
    GIS_ZHKH
    + "/homemanagement/api/rest/services/houses/public/houses"
    + "/searchByFiasHouseCodeList/{}?useReadOnlyDataSource=true"
)
PASSPORT_URL = (
    GIS_ZHKH
    + "/homemanagement/api/rest/services/houses/public/{type}/{guid}"
    + "?useReadOnlyDataSource=true"
)
BATCH_SIZE = 100  # 300 кодов -> HTTP 414 Request-URI Too Large
BATCH_PAUSE = 0.3  # сек между пачками
PASSPORT_THREADS = 6
PASSPORT_PAUSE = 0.25  # сек между запросами в потоке
PASSPORT_RETRIES = 3
MATCH_RADIUS_M = 50

HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

CENTROID_RE = re.compile(r"coordinates=\[([\d.]+),\s*([\d.]+)\]")

# ---------------------------------------------------------------------------
# Фаза 1: адресный реестр -> точки с FIAS
# ---------------------------------------------------------------------------


def read_registry(district_substr: str) -> list[dict]:
    """Фильтр реестра по DISTRICT и типу «здание».

    Возвращает список {unom, fias, lon, lat, address}, уникальный по fias.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    with zipfile.ZipFile(REGISTRY_ZIP) as z:
        with z.open(z.namelist()[0]) as f:
            reader = csv.reader(
                io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace"),
                delimiter=";",
            )
            header = [h.strip('"') for h in next(reader)]
            i_dist = header.index("DISTRICT")
            i_type = header.index("Object type")
            i_unom = header.index("UNOM")
            i_addr = header.index("ADDRESS")
            i_fias = header.index("N_FIAS")
            i_cen = header.index("Centroid")
            for row in reader:
                if len(row) <= i_cen:
                    continue
                if district_substr not in row[i_dist] or row[i_type] != "здание":
                    continue
                fias = row[i_fias].strip().lower()
                m = CENTROID_RE.search(row[i_cen])
                if not fias or fias in seen or not m:
                    continue
                seen.add(fias)
                rows.append(
                    {
                        "unom": int(row[i_unom]),
                        "fias": fias,
                        "lon": float(m.group(1)),
                        "lat": float(m.group(2)),
                        "address": row[i_addr],
                    }
                )
    return rows


# ---------------------------------------------------------------------------
# Фаза 2: пакетный обмен FIAS -> GUID дома
# ---------------------------------------------------------------------------


def exchange_batch(codes: list[str]) -> dict:
    url = BATCH_URL.format(",".join(codes))
    for attempt in range(PASSPORT_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                return r.json()
            time.sleep(1.5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    print(f"WARN: пачка из {len(codes)} кодов не ответила, пропуск", file=sys.stderr)
    return {}


def exchange(fias_list: list[str]) -> dict:
    """{fias_lower: {guid, type_code, address, cadastre}} для найденных домов."""
    found: dict[str, dict] = {}
    for i in range(0, len(fias_list), BATCH_SIZE):
        batch = fias_list[i : i + BATCH_SIZE]
        data = exchange_batch(batch)
        for h in data.get("houseList") or []:
            house = h.get("house") or {}
            fias = (house.get("code") or "").lower()
            if not fias:
                continue
            found[fias] = {
                "guid": h.get("guid"),
                "type_code": (h.get("houseType") or {}).get("code"),
                "address": h.get("formattedAddress"),
                "cadastre": h.get("cadastreNumber"),
            }
        time.sleep(BATCH_PAUSE)
        done = min(i + BATCH_SIZE, len(fias_list))
        if (done // BATCH_SIZE) % 5 == 0 or done == len(fias_list):
            print(f"  exchange: {done}/{len(fias_list)} кодов, найдено домов {len(found)}")
    return found


# ---------------------------------------------------------------------------
# Фаза 3: паспорта домов (кэшируемо, многопоточно)
# ---------------------------------------------------------------------------

_tls = threading.local()


def session() -> requests.Session:
    """keep-alive-сессия на поток (WinError 10048 при пуле по умолчанию)."""
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        _tls.s = s
    return s


def material_str(value) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        parts = []
        for m in value:
            if isinstance(m, dict):
                parts.append(str(m.get("value") or m.get("name") or ""))
            else:
                parts.append(str(m))
        return ", ".join(p for p in parts if p) or None
    return str(value)


def fetch_passport(type_code: str, guid: str) -> dict | None:
    url = PASSPORT_URL.format(type=type_code, guid=guid)
    for attempt in range(PASSPORT_RETRIES):
        try:
            r = session().get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (404, 400):
                return None
            time.sleep(1.5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None


def passports(houses: dict[str, dict]) -> dict[str, dict]:
    """{fias: паспорт-поля}. Докачивает недостающее из PASSPORT_CACHE."""
    cache: dict[str, dict] = {}
    if PASSPORT_CACHE.exists():
        for line in PASSPORT_CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[rec["fias"]] = rec
    todo = [f for f, h in houses.items() if h.get("guid") and f not in cache]
    print(f"passports: в кэше {len(cache)}, к загрузке {len(todo)}")

    lock = threading.Lock()
    counter = {"done": 0}
    todo_iter = iter(todo)

    def worker() -> None:
        while True:
            with lock:
                fias = next(todo_iter, None)
            if fias is None:
                return
            h = houses[fias]
            p = fetch_passport(h["type_code"], h["guid"])
            rec = {"fias": fias, "found": p is not None}
            if p is not None:
                try:
                    rec["building_year"] = int(p["buildingYear"]) if p.get("buildingYear") else None
                except (ValueError, TypeError):
                    rec["building_year"] = None
                rec["operation_year"] = p.get("operationYear")
                rec["series"] = p.get("planSeries")
                rec["material"] = material_str(p.get("intWallMaterialList"))
            with lock:
                cache[fias] = rec
                with PASSPORT_CACHE.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counter["done"] += 1
                if counter["done"] % 100 == 0:
                    print(f"  passports: {counter['done']}/{len(todo)}")
            time.sleep(PASSPORT_PAUSE)

    threads = [threading.Thread(target=worker) for _ in range(PASSPORT_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return cache


# ---------------------------------------------------------------------------
# Фаза 4: PostGIS — building_years + spatial join к buildings_presnya
# ---------------------------------------------------------------------------

CREATE_YEARS = """
CREATE TABLE building_years (
    fias_guid     TEXT PRIMARY KEY,
    unom          BIGINT,
    address       TEXT,
    building_year SMALLINT,
    operation_year SMALLINT,
    series        TEXT,
    material      TEXT,
    geom          GEOMETRY(POINT, 4326) NOT NULL
)
"""

CREATE_MATCH = f"""
CREATE TABLE building_year_match AS
SELECT DISTINCT ON (b.osm_id)
       b.osm_id,
       y.building_year,
       round(ST_DistanceSphere(ST_PointOnSurface(b.geometry), y.geom)) AS distance_m
FROM buildings_presnya b
JOIN building_years y
  ON ST_DWithin(
       ST_PointOnSurface(b.geometry)::geography,
       y.geom::geography,
       {MATCH_RADIUS_M}
     )
ORDER BY b.osm_id, ST_DistanceSphere(ST_PointOnSurface(b.geometry), y.geom)
"""


def load_db(registry: list[dict], houses: dict, cache: dict) -> None:
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS building_year_match")
        cur.execute("DROP TABLE IF EXISTS building_years")
        cur.execute(CREATE_YEARS)
        n = 0
        for r in registry:
            h = houses.get(r["fias"])
            rec = cache.get(r["fias"])
            if not h or not rec or not rec.get("found"):
                continue
            cur.execute(
                """
                INSERT INTO building_years
                    (fias_guid, unom, address, building_year, operation_year,
                     series, material, geom)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ON CONFLICT (fias_guid) DO NOTHING
                """,
                (
                    r["fias"],
                    r["unom"],
                    h.get("address") or r["address"],
                    rec.get("building_year"),
                    rec.get("operation_year"),
                    rec.get("series"),
                    rec.get("material"),
                    r["lon"],
                    r["lat"],
                ),
            )
            n += 1
        cur.execute(CREATE_MATCH)
        cur.execute("SELECT count(*) FROM building_years")
        years_n = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM building_year_match")
        match_n = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM buildings_presnya")
        total = cur.fetchone()[0]
        conn.commit()
    print(
        f"building_years: {years_n} домов с паспортами; "
        f"привязано к зданиям OSM: {match_n}/{total}"
    )


# ---------------------------------------------------------------------------


def main() -> int:
    phases = sys.argv[1:] or ["registry", "exchange", "passports", "load"]
    district = "Пресненск"

    registry: list[dict] = []
    houses: dict[str, dict] = {}
    cache: dict[str, dict] = {}

    if "registry" in phases:
        print("1/4: читаю адресный реестр...")
        registry = read_registry(district)
        PROC.mkdir(parents=True, exist_ok=True)
        REGISTRY_JSON.write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  зданий с FIAS в районе: {len(registry)}")
    elif REGISTRY_JSON.exists():
        registry = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))

    if "exchange" in phases:
        print("2/4: пакетный обмен FIAS -> GUID ГИС ЖКХ...")
        houses = exchange([r["fias"] for r in registry])
        HOUSES_JSON.write_text(json.dumps(houses, ensure_ascii=False), encoding="utf-8")
        print(f"  найдено домов: {len(houses)}/{len(registry)}")
    elif HOUSES_JSON.exists():
        houses = json.loads(HOUSES_JSON.read_text(encoding="utf-8"))

    if "passports" in phases:
        print("3/4: паспорта домов...")
        cache = passports(houses)
    elif PASSPORT_CACHE.exists():
        cache = {
            json.loads(line)["fias"]: json.loads(line)
            for line in PASSPORT_CACHE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    if "load" in phases:
        if not registry or not houses:
            print("ERR: для фазы load нужны registry и exchange", file=sys.stderr)
            return 1
        print("4/4: загрузка в PostGIS и spatial join...")
        load_db(registry, houses, cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
