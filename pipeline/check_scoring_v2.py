#!/usr/bin/env python3
"""Замер v2: распределение балла до/после на выборке жилых домов.

«До» — текущий scores.index (v1, legacy). «После» — v2 из scoring_v2,
посчитанный теми же SQL-функциями, что и scoring.py. Ничего не пишет в БД.

  python pipeline/check_scoring_v2.py            # 5000 домов
  python pipeline/check_scoring_v2.py --all      # все дома со scores
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring
import scoring_v2
from db import DB_DSN


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замер скоринга v2")
    parser.add_argument("--sample", type=int, default=5000)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    cfg = scoring.load_config()
    limit = "" if args.all else "LIMIT %s"
    params = [] if args.all else [args.sample]
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT b.osm_id, s.index, "
            "(s.breakdown->'penalties'->'industrial') IS NOT NULL "
            "FROM buildings b "
            "JOIN moscow m ON ST_Intersects(b.geometry, m.geometry) "
            "JOIN scores s ON s.building_id = b.osm_id "
            "WHERE b.is_home "
            "ORDER BY md5(b.osm_id::text) "
            + limit,
            params,
        )
        rows = cur.fetchall()
        ids = [row[0] for row in rows]
        before = {row[0]: float(row[1]) for row in rows}
        legacy_industrial = {row[0]: bool(row[2]) for row in rows}

        scope = "(SELECT * FROM buildings WHERE osm_id = ANY(%s))"
        transport = scoring.collect_v2_transport(cur, scope, [ids], cfg)
        industry = scoring.collect_v2_industry(cur, scope, [ids], cfg)
        access = scoring.collect_v2_access(cur, scope, [ids], cfg)
        green = scoring.collect_v2_green(cur, scope, [ids], cfg)
        home = scoring.collect_v2_home(cur, scope, [ids], cfg)
        density = scoring.collect_v2_density(cur, scope, [ids], cfg)
        wind = scoring.collect_v2_wind(
            cur, scope, [ids], cfg, scoring.load_wind_rose()
        )

    empty_transport = {"value": 0.0, "status": "outside", "class": None}
    empty_industry = scoring_v2.industry_pressure([], cfg)
    empty_access = scoring_v2.access_quality(
        {name: None for name in cfg["v2"]["access"]["types"]}, cfg
    )
    empty_green = scoring_v2.green_quality([], cfg)
    empty_home = scoring_v2.home_quality(None, None, None, cfg)
    empty_density = scoring_v2.density_pressure(None, cfg)
    empty_wind = scoring_v2.wind_pressure([], scoring.load_wind_rose(), cfg)

    after: list[float] = []
    without_industry: list[float] = []
    deltas: list[float] = []
    factor_values = {"transport": [], "industry": [], "access": [], "green": [],
                     "home": [], "density": [], "wind": []}
    flags = {"transport:not_classified": 0, "industry:not_classified": 0,
             "industry:unknown_status": 0,
             "access:not_classified": 0, "green:not_classified": 0,
             "home:not_classified": 0, "density:not_classified": 0,
             "wind:not_classified": 0}
    industry_penalized = 0
    wind_positive = 0
    for osm_id in ids:
        wind_entry = wind.get(osm_id, empty_wind)
        block = scoring_v2.build_v2_block(
            transport.get(osm_id, empty_transport),
            access.get(osm_id, empty_access),
            green.get(osm_id, empty_green),
            home.get(osm_id, empty_home),
            density.get(osm_id, empty_density),
            cfg,
            industry=industry.get(osm_id, empty_industry),
            wind=wind_entry,
        )
        reference = scoring_v2.build_v2_block(
            transport.get(osm_id, empty_transport),
            access.get(osm_id, empty_access),
            green.get(osm_id, empty_green),
            home.get(osm_id, empty_home),
            density.get(osm_id, empty_density),
            cfg,
            wind=wind_entry,
        )
        value = float(block["index"])
        after.append(value)
        without_industry.append(float(reference["index"]))
        deltas.append(abs(value - before[osm_id]))
        if block["factors"]["industry"]["value"] > 0:
            industry_penalized += 1
        if block["factors"]["wind"]["value"] > 0:
            wind_positive += 1
        for name, entry in block["factors"].items():
            factor_values[name].append(float(entry["value"]))
        for flag in block["flags"]:
            flags[flag] = flags.get(flag, 0) + 1

    def report(name: str, values: list[float]) -> None:
        print(
            f"{name}: min {min(values):.2f}  "
            f"p25 {percentile(values, 0.25):.2f}  "
            f"медиана {statistics.median(values):.2f}  "
            f"p75 {percentile(values, 0.75):.2f}  "
            f"max {max(values):.2f}"
        )

    print(f"Домов: {len(ids):,}")
    report("до  (v1 scores)", [before[i] for i in ids])
    report("v2 без промзон", without_industry)
    report("v2 с промзонами", after)
    legacy_share = sum(1 for flag in legacy_industrial.values() if flag) / len(ids)
    print(f"штраф v1 landuse=industrial: {legacy_share * 100:.1f}% домов")
    print(f"v2 industry value > 0: {industry_penalized / len(ids) * 100:.1f}% домов")
    print(f"v2 wind value > 0: {wind_positive / len(ids) * 100:.1f}% домов")
    moved = sum(1 for delta in deltas if delta > 1.0)
    print(f"delta v1->v2 > 1 пункта: {moved:,} ({moved / len(ids) * 100:.1f}%)")
    print(f"медиана |delta|: {statistics.median(deltas):.2f}")
    print(f"max |delta|: {max(deltas):.2f}; delta != 0: {sum(1 for d in deltas if d > 1e-9):,}")
    print("среднее value: " + ", ".join(
        f"{name} {statistics.mean(values):.3f}"
        for name, values in factor_values.items()))
    print("флаги not_classified: " + ", ".join(
        f"{name} {count:,}" for name, count in flags.items() if count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
