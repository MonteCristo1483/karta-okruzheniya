#!/usr/bin/env python3
"""Скоринг «Индекс окружения» (SPEC §3). Все коэффициенты — из config/scoring.json.

Пешее время до POI считается по прямой: мин = м / (walking_speed_kmh × 1000 / 60)
(маршруты Valhalla — позже). Штраф начисляется только при расстоянии < радиуса
слоя. Слои с enabled: false (стройка, промзона) пропускаются.

Использование:
  python scoring.py             — все здания buildings_presnya
  python scoring.py 123 456 ... — конкретные osm_id из buildings (для T12)
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "scoring.json"
DB_DSN = "host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"
BATCH_SIZE = 5_000

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scores (
    building_id BIGINT PRIMARY KEY,
    index       NUMERIC(4, 1) NOT NULL,
    breakdown   JSONB NOT NULL
)
"""

UPSERT = """
INSERT INTO scores (building_id, index, breakdown)
VALUES (%s, %s, %s)
ON CONFLICT (building_id) DO UPDATE
SET index = EXCLUDED.index, breakdown = EXCLUDED.breakdown
"""

BUILDING_IDS = "SELECT osm_id FROM {scope}"

NEAREST_POI = """
SELECT b.osm_id, ST_Distance(b.geometry::geography, p.geometry::geography)
FROM {scope} b
CROSS JOIN LATERAL (
    SELECT p.geometry FROM poi p
    WHERE p.type = ANY(%s)
    ORDER BY p.geometry <-> b.geometry
    LIMIT 1
) p
"""

NEAREST_LINEAR = """
SELECT b.osm_id,
       ST_Distance(b.geometry::geography, l.geometry::geography),
       l.class
FROM {scope} b
CROSS JOIN LATERAL (
    SELECT l.geometry, l.class FROM linear l
    WHERE l.kind = %s AND l.class = ANY(%s)
    ORDER BY l.geometry <-> b.geometry
    LIMIT 1
) l
"""


def dec(value) -> Decimal:
    return Decimal(str(value))


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def round_dec(value: Decimal, places: int = 1) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def walking_minutes(distance_m: Decimal, speed_kmh: Decimal) -> Decimal:
    return distance_m / (speed_kmh * 1000 / 60)


def multiplier_for(minutes: Decimal, thresholds: list[dict]) -> Decimal:
    for threshold in sorted(thresholds, key=lambda t: t["up_to"]):
        if minutes <= dec(threshold["up_to"]):
            return dec(threshold["multiplier"])
    return Decimal(0)


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def scope_from_args(argv: list[str]) -> tuple[str, list]:
    if not argv:
        return "buildings_presnya", []
    ids = [int(a) for a in argv]
    return "(SELECT * FROM buildings WHERE osm_id = ANY(%s))", [ids]


def collect_bonuses(cur, scope: str, scope_params: list, cfg: dict) -> dict:
    speed = dec(cfg["bonuses"]["walking_speed_kmh"])
    thresholds = cfg["bonuses"]["time_thresholds_min"]
    bonuses: dict[str, dict] = {}
    for category, cat_cfg in cfg["bonuses"]["categories"].items():
        cur.execute(NEAREST_POI.format(scope=scope), [*scope_params, cat_cfg["poi_types"]])
        category_max = dec(cat_cfg["max"])
        per_building: dict[int, dict] = {}
        for osm_id, dist in cur.fetchall():
            distance = dec(dist)
            minutes = walking_minutes(distance, speed)
            multiplier = multiplier_for(minutes, thresholds)
            per_building[osm_id] = {
                "distance_m": float(round_dec(distance)),
                "minutes": float(round_dec(minutes)),
                "multiplier": float(multiplier),
                "bonus": float(category_max * multiplier),
            }
        bonuses[category] = per_building
    return bonuses


def collect_penalties(cur, scope: str, scope_params: list, cfg: dict) -> tuple[dict, list]:
    penalties: dict[str, dict] = {}
    skipped: list[str] = []
    for name, pen_cfg in cfg["penalties"].items():
        if not isinstance(pen_cfg, dict):
            continue
        if not pen_cfg.get("enabled", False):
            skipped.append(name)
            continue
        classes = pen_cfg["classes"]
        if isinstance(classes, dict):
            class_list = list(classes)
            weight_of = {cls: dec(w) for cls, w in classes.items()}
        else:
            class_list = list(classes)
            weight_of = {}
        cur.execute(
            NEAREST_LINEAR.format(scope=scope),
            [*scope_params, name, class_list],
        )
        radius = dec(pen_cfg["radius_m"])
        penalty_max = dec(pen_cfg["max"])
        per_building: dict[int, dict] = {}
        for osm_id, dist, cls in cur.fetchall():
            distance = dec(dist)
            weight = weight_of.get(cls, Decimal(1))
            penalty = penalty_max * weight * (1 - distance / radius) if distance < radius else Decimal(0)
            per_building[osm_id] = {
                "distance_m": float(round_dec(distance)),
                "class": cls,
                "weight": float(weight),
                "penalty": float(round_dec(penalty, 3)),
            }
        penalties[name] = per_building
    return penalties, skipped


def main(argv: list[str]) -> int:
    cfg = load_config()
    base = dec(cfg["index"]["base"])
    low = dec(cfg["index"]["min"])
    high = dec(cfg["index"]["max"])
    decimals = int(cfg["index"]["rounding_decimals"])
    scope, scope_params = scope_from_args(argv)

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
        cur.execute(BUILDING_IDS.format(scope=scope), scope_params)
        building_ids = [row[0] for row in cur.fetchall()]

        bonuses = collect_bonuses(cur, scope, scope_params, cfg)
        penalties, skipped = collect_penalties(cur, scope, scope_params, cfg)

        buffer: list[tuple] = []
        for osm_id in building_ids:
            breakdown: dict = {"base": float(base), "bonuses": {}, "penalties": {}}
            total = base
            for category, per_building in bonuses.items():
                entry = per_building.get(osm_id)
                breakdown["bonuses"][category] = entry
                total += dec(entry["bonus"]) if entry else Decimal(0)
            for name, per_building in penalties.items():
                entry = per_building.get(osm_id)
                breakdown["penalties"][name] = entry
                total += dec(entry["penalty"]) if entry else Decimal(0)
            index = round_dec(clamp(total, low, high), decimals)
            breakdown["index"] = float(index)
            buffer.append((osm_id, index, Jsonb(breakdown)))
            if len(buffer) >= BATCH_SIZE:
                cur.executemany(UPSERT, buffer)
                buffer.clear()
        if buffer:
            cur.executemany(UPSERT, buffer)
        conn.commit()

    print(
        f"Готово: {len(building_ids):,} зданий, слои disabled пропущены: "
        f"{', '.join(skipped) if skipped else 'нет'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
