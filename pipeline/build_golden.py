#!/usr/bin/env python3
"""Сборка golden set фактов (Эталон-2, шаг 2).

Выбирает дома стратифицированно и записывает проверяемые факты в
`data/golden/facts.json`:

  * транспорт «есть» — рядом официальный вход метро (data.mos.ru, набор 624);
  * транспорт «нет» — официального входа нет в радиусе 1.2 км;
  * порог магистрали — расстояние 190–210 м вокруг радиуса 200 м;
  * стройка/промзона «есть» — штраф заметно меньше нуля.

Важно: это якоря регрессии, а не «правильная оценка». Независимый источник
только у транспорта (официальные входы); остальные факты пока из OSM и
помечены confidence=medium. Golden set не меняется вместе с формулой и
версионируется отдельно от config/scoring.json.

  python pipeline/build_golden.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import features  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "golden" / "facts.json"
CONFIG = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
DISTRICT = "Пресненский"
TRANSPORT_NEAR_M = 400.0
TRANSPORT_FAR_M = 1200.0
CONSTRUCTION_CONFIRM_M = 150.0
ROAD_RADIUS = float(CONFIG["penalties"]["road"]["radius_m"])
ROAD_BAND_M = 10.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def official_entrances() -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    skip = 0
    while True:
        page = features(624, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if DISTRICT not in str(attributes.get("District", "")):
                continue
            coordinates = (feature.get("geometry") or {}).get("coordinates")
            if coordinates:
                points.append((float(coordinates[1]), float(coordinates[0])))
        if len(page) < 1000:
            break
        skip += 1000
    return points


def nearest_m(lat: float, lon: float, points: list[tuple[float, float]]) -> float:
    return min((haversine_m(lat, lon, plat, plon) for plat, plon in points),
               default=float("inf"))


def official_construction() -> list[tuple[float, float]]:
    """Точки официальных объектов «в строительстве» (набор 2941)."""
    points: list[tuple[float, float]] = []
    skip = 0
    while True:
        page = features(2941, top=1000, skip=skip).get("features") or []
        for feature in page:
            attributes = (feature.get("properties") or {}).get("attributes") or {}
            if DISTRICT not in str(attributes.get("District", "")):
                continue
            if "в строительстве" not in str(attributes.get("ObjectStatus", "")):
                continue
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if not coordinates:
                continue
            point = coordinates[0] if isinstance(coordinates[0], (list, tuple)) else coordinates
            points.append((float(point[1]), float(point[0])))
        if len(page) < 1000:
            break
        skip += 1000
    return points


def spread(items: list[dict], count: int) -> list[dict]:
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int(index * step)] for index in range(count)]


def load_buildings() -> list[dict]:
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT b.osm_id, b.addr_street, b.addr_housenumber,
                   ST_Y(ST_Centroid(b.geometry)), ST_X(ST_Centroid(b.geometry)),
                   s.breakdown
            FROM buildings_presnya b
            JOIN scores s ON s.building_id = b.osm_id
        """)
        rows = []
        for osm_id, street, house, lat, lon, breakdown in cur.fetchall():
            address = ", ".join(part for part in (street, house) if part) or None
            rows.append({
                "building_id": osm_id,
                "address": address,
                "lat": lat,
                "lon": lon,
                "breakdown": breakdown,
            })
        return rows


def transport_entry(building: dict) -> dict | None:
    return (building["breakdown"].get("bonuses") or {}).get("transport")


def penalty_entry(building: dict, name: str) -> dict | None:
    return (building["breakdown"].get("penalties") or {}).get(name)


def main() -> int:
    entrances = official_entrances()
    if not entrances:
        print("Нет официальных входов метро — проверьте ключ и набор 624",
              file=sys.stderr)
        return 1

    buildings = load_buildings()
    for building in buildings:
        building["official_m"] = nearest_m(building["lat"], building["lon"], entrances)

    facts: list[dict] = []
    today = date.today().isoformat()

    def add(building: dict, factor: str, kind: str, expect: str, distance: float,
            source: str, confidence: str, check_distance: bool,
            tolerance: float) -> None:
        facts.append({
            "building_id": building["building_id"],
            "address": building["address"],
            "factor": factor,
            "type": kind,
            "expect": expect,
            "distance_m": round(distance, 1),
            "tolerance_m": tolerance,
            "check_distance": check_distance,
            "source": source,
            "confidence": confidence,
            "retrieved_at": today,
        })

    near = sorted(
        [b for b in buildings
         if b["official_m"] <= TRANSPORT_NEAR_M
         and (transport_entry(b) or {}).get("bonus", 0) > 0],
        key=lambda b: b["official_m"],
    )
    for building in spread(near, 6):
        add(building, "transport", "bonus", "positive", building["official_m"],
            "data.mos.ru/624", "high", False, 0)

    far = sorted(
        [b for b in buildings
         if b["official_m"] >= TRANSPORT_FAR_M
         and (transport_entry(b) or {}).get("bonus", 0) == 0],
        key=lambda b: -b["official_m"],
    )
    for building in spread(far, 5):
        add(building, "transport", "bonus", "zero", building["official_m"],
            "data.mos.ru/624", "high", False, 0)

    boundary = sorted(
        [b for b in buildings
         if (penalty_entry(b, "road") or {}).get("distance_m") is not None
         and abs((penalty_entry(b, "road") or {})["distance_m"] - ROAD_RADIUS)
         <= ROAD_BAND_M],
        key=lambda b: (penalty_entry(b, "road") or {})["distance_m"],
    )
    for building in spread(boundary, 4):
        entry = penalty_entry(building, "road") or {}
        distance = float(entry["distance_m"])
        expect = "zero" if distance >= ROAD_RADIUS else "positive"
        add(building, "road", "penalty", expect, distance,
            "osm/linear", "medium", True, 5.0)

    zones = [b for b in buildings
             if (penalty_entry(b, "construction") or {}).get("penalty", 0) < -0.1]
    construction_points = official_construction()
    for building in zones:
        building["official_construction_m"] = nearest_m(
            building["lat"], building["lon"], construction_points)
    confirmed = sorted(
        [b for b in zones if b["official_construction_m"] <= CONSTRUCTION_CONFIRM_M],
        key=lambda b: b["official_construction_m"],
    )
    chosen = spread(confirmed, 3)
    if len(chosen) < 3:
        rest = sorted(
            [b for b in zones if b not in confirmed],
            key=lambda b: b["building_id"],
        )
        chosen += spread(rest, 3 - len(chosen))
    for building in chosen:
        entry = penalty_entry(building, "construction") or {}
        official_m = building.get("official_construction_m")
        confirmed_fact = official_m is not None and official_m <= CONSTRUCTION_CONFIRM_M
        add(
            building, "construction", "penalty", "positive",
            float(entry["distance_m"]),
            "data.mos.ru/2941 + osm/zones" if confirmed_fact else "osm/zones",
            "high" if confirmed_fact else "medium",
            True, 5.0,
        )

    payload = {
        "generated_at": today,
        "note": ("Якоря регрессии. Независимые источники: входы метро "
                 "(data.mos.ru/624) и стройка (data.mos.ru/2941). "
                 "Магистраль — пока OSM (medium). Машиночитаемого реестра "
                 "промзон на портале нет: industrial в факты не включён."),
        "facts": facts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"Записано {len(facts)} фактов: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
