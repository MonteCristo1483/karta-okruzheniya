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
            "SELECT b.osm_id, s.index "
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

        scope = "(SELECT * FROM buildings WHERE osm_id = ANY(%s))"
        transport = scoring.collect_v2_transport(cur, scope, [ids], cfg)
        green = scoring.collect_v2_green(cur, scope, [ids], cfg)
        home = scoring.collect_v2_home(cur, scope, [ids], cfg)
        density = scoring.collect_v2_density(cur, scope, [ids], cfg)

    empty_transport = {"value": 0.0, "status": "outside", "class": None}
    empty_green = scoring_v2.green_quality([], cfg)
    empty_home = scoring_v2.home_quality(None, None, None, cfg)
    empty_density = scoring_v2.density_pressure(None, cfg)

    after: list[float] = []
    deltas: list[float] = []
    factor_values = {"transport": [], "green": [], "home": [], "density": []}
    flags = {"transport:not_classified": 0, "green:not_classified": 0,
             "home:not_classified": 0, "density:not_classified": 0}
    for osm_id in ids:
        block = scoring_v2.build_v2_block(
            transport.get(osm_id, empty_transport),
            green.get(osm_id, empty_green),
            home.get(osm_id, empty_home),
            density.get(osm_id, empty_density),
            cfg,
        )
        value = float(block["index"])
        after.append(value)
        deltas.append(abs(value - before[osm_id]))
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
    report("до  (v1)", [before[i] for i in ids])
    report("после (v2)", after)
    moved = sum(1 for delta in deltas if delta > 1.0)
    print(f"delta > 1 пункта: {moved:,} ({moved / len(ids) * 100:.1f}%)")
    print(f"медиана |delta|: {statistics.median(deltas):.2f}")
    print("среднее value: " + ", ".join(
        f"{name} {statistics.mean(values):.3f}"
        for name, values in factor_values.items()))
    print("флаги not_classified: " + ", ".join(
        f"{name} {count:,}" for name, count in flags.items() if count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
