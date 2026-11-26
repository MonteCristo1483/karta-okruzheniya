#!/usr/bin/env python3
"""Golden set v2: 18 якорей для четырёх факторов METHODOLOGY-V2.

Выборка — по сырым данным (класс дороги + расстояние, год из реестра,
плотность из footprint*этажность, наличие парка), а не по готовому баллу.
Факты проверяются `check_golden.py` по breakdown.v2.factors.

  18 фактов: транспорт 10 (6 «есть», 4 «нет»), дом 5 (3 с годом,
  2 без года), плотность 2 (высокая/низкая), зелень 1 (площадь неизвестна).

  python pipeline/build_golden_v2.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring
import scoring_v2
from db import DB_DSN

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "golden" / "facts.json"
CONFIG = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
SAMPLE = 4000


def spread(items: list[dict], count: int) -> list[dict]:
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int(index * step)] for index in range(count)]


def load_homes(cur) -> list[dict]:
    cur.execute(
        "SELECT b.osm_id, b.addr_street, b.addr_housenumber "
        "FROM buildings b "
        "JOIN moscow m ON ST_Intersects(b.geometry, m.geometry) "
        "JOIN scores s ON s.building_id = b.osm_id "
        "WHERE b.is_home "
        "ORDER BY md5(b.osm_id::text) LIMIT %s",
        [SAMPLE],
    )
    return [
        {
            "building_id": osm_id,
            "address": ", ".join(p for p in (street, house) if p) or None,
        }
        for osm_id, street, house in cur.fetchall()
    ]


def transport_distances(cur, ids: list[int], cfg: dict) -> dict[int, dict]:
    classes = list(cfg["v2"]["transport"]["classes"])
    cur.execute(
        """
        SELECT b.osm_id, c.class,
               ST_Distance(b.geometry::geography, l.geometry::geography)
        FROM (SELECT * FROM buildings WHERE osm_id = ANY(%s)) b
        CROSS JOIN unnest(%s::text[]) AS c(class)
        CROSS JOIN LATERAL (
            SELECT geometry FROM linear
            WHERE kind = 'road' AND class = c.class
            ORDER BY geometry <-> b.geometry LIMIT 1
        ) l
        """,
        [ids, classes],
    )
    result: dict[int, dict] = {}
    for osm_id, cls, distance in cur.fetchall():
        result.setdefault(osm_id, {})[cls] = float(distance)
    return result


def home_years(cur, ids: list[int]) -> dict[int, int | None]:
    cur.execute(
        "SELECT osm_id, building_year FROM building_year_match "
        "WHERE osm_id = ANY(%s) AND distance_m <= %s",
        [ids, CONFIG["v2"]["home"]["match_radius_m"]],
    )
    return {osm_id: year for osm_id, year in cur.fetchall()}


def build(cur) -> list[dict]:
    homes = load_homes(cur)
    ids = [home["building_id"] for home in homes]
    by_id = {home["building_id"]: home for home in homes}
    today = date.today().isoformat()
    facts: list[dict] = []

    def add(building: dict, factor: str, expect: str, distance: float,
            source: str, confidence: str, check_distance: bool,
            flag: str | None = None) -> None:
        facts.append({
            "building_id": building["building_id"],
            "address": building["address"],
            "factor": factor,
            "type": "factor",
            "expect": expect,
            "distance_m": round(distance, 1),
            "tolerance_m": 5.0 if check_distance else 0,
            "check_distance": check_distance,
            "source": source,
            "confidence": confidence,
            "retrieved_at": today,
            **({"flag": flag} if flag else {}),
        })

    # 1. Транспорт: 6 «есть» (value > 0.2) и 4 «нет» (все классы вне R).
    distances = transport_distances(cur, ids, CONFIG)
    positive, zero = [], []
    for osm_id, per_class in distances.items():
        entry = scoring_v2.best_transport_quality(per_class, CONFIG)
        if entry["value"] >= 0.2:
            positive.append({**by_id[osm_id], "entry": entry})
        if all(
            per_class.get(cls, 1e9) >= spec["radius_m"]
            for cls, spec in CONFIG["v2"]["transport"]["classes"].items()
        ):
            zero.append({**by_id[osm_id], "entry": entry})
    positive.sort(key=lambda item: item["entry"]["value"])
    zero.sort(key=lambda item: item["building_id"])
    for building in spread(positive, 6):
        entry = building["entry"]
        add(building, "transport", "positive", entry["distance_m"],
            "osm/linear", "medium", True)
    for building in spread(zero, 4):
        entry = building["entry"]
        # Все классы вне R; расстояние не якорь (класс с нулём неоднозначен).
        add(building, "transport", "zero", entry["distance_m"],
            "osm/linear", "medium", False)

    # 2. Дом: 3 с годом (разные эпохи) и 2 без года.
    years = home_years(cur, ids)
    known = [by_id[osm_id] | {"year": year} for osm_id, year in years.items()]
    old = sorted([b for b in known if b["year"] <= 1969], key=lambda b: b["year"])
    middle = sorted([b for b in known if 1970 <= b["year"] <= 1990],
                    key=lambda b: b["year"])
    new = sorted([b for b in known if b["year"] >= 2015], key=lambda b: b["year"])
    if old:
        add(old[0], "home", "positive", 0, "building_year_match", "high", False)
    if middle:
        add(middle[len(middle) // 2], "home", "positive", 0,
            "building_year_match", "high", False)
    if new:
        add(new[-1], "home", "positive", 0, "building_year_match", "high", False)
    unknown = [by_id[osm_id] for osm_id in ids if osm_id not in years]
    for building in spread(unknown, 2):
        add(building, "home", "zero", 0, "building_year_match", "high", False,
            flag="home:not_classified")

    # 3. Плотность: 1 высокая (давление 1) и 1 низкая (давление 0).
    radius = CONFIG["v2"]["density"]["radius_m"]
    density = scoring.collect_v2_density(
        cur, "(SELECT * FROM buildings WHERE osm_id = ANY(%s))", [ids], CONFIG
    )
    high = [by_id[osm_id] | {"entry": entry}
            for osm_id, entry in density.items() if entry["value"] >= 1.0]
    low = [by_id[osm_id] | {"entry": entry}
           for osm_id, entry in density.items() if entry["value"] == 0]
    high.sort(key=lambda b: b["entry"]["density_m2_per_ha"], reverse=True)
    low.sort(key=lambda b: b["entry"]["density_m2_per_ha"])
    if high:
        add(high[0], "density", "positive", 0, "osm/footprints", "medium", False)
    if low:
        add(low[0], "density", "zero", 0, "osm/footprints", "medium", False)

    # 4. Зелень: парк в 300 м, площадь неизвестна -> not_classified.
    green = scoring.collect_v2_green(
        cur, "(SELECT * FROM buildings WHERE osm_id = ANY(%s))", [ids], CONFIG
    )
    flagged = [by_id[osm_id] | {"entry": entry}
               for osm_id, entry in green.items()
               if entry["status"] == "not_classified"]
    if flagged:
        add(flagged[0], "green", "zero", flagged[0]["entry"]["distance_m"],
            "osm/poi parks", "medium", True, flag="green:not_classified")

    return facts


def main() -> int:
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        facts = build(cur)
    if len(facts) != 18:
        print(f"Получено {len(facts)} фактов вместо 18", file=sys.stderr)
        return 1
    payload = {
        "generated_at": date.today().isoformat(),
        "note": ("Golden v2: 18 якорей четырёх факторов METHODOLOGY-V2. "
                 "Источники: OSM/linear (класс и расстояние), реестр годов, "
                 "OSM/footprints (плотность), OSM/poi parks (площадь парка "
                 "в данных не хранится -> not_classified)."),
        "facts": facts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"Записано {len(facts)} фактов: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
