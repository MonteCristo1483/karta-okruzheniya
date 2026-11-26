#!/usr/bin/env python3
"""Сборка артефактов одного района Москвы (V2-S1).

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/build_district.py --district Пресненский
  pipeline/.venv/Scripts/python.exe pipeline/build_district.py --district 1275551

Делает для любого района то, что раньше делалось руками для Пресни:
  * применяет migrations/004_district_views.sql (идемпотентно);
  * считает балл только жилым (is_home = TRUE) через scoring.py;
  * выгружает buildings/poi/redzones через export_map_data.collect();
  * пишет buildings-index.json только по жилым;
  * собирает PMTiles через build_tiles (tippecanoe) — можно отключить
    флагом --no-tiles;
  * кладёт geojson границы района.

Маршруты и изохроны не считаются (шаг 2). В web/ ничего не пишется:
по умолчанию результат в data/districts/<osm_id>/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import DB_DSN  # noqa: E402
import export_map_data as export  # noqa: E402
import scoring  # noqa: E402
from build_tiles import (  # noqa: E402
    build_index,
    human_size,
    pmtiles_stats,
    run_tippecanoe,
    write_ndjson,
)

MIGRATION = ROOT / "pipeline" / "migrations" / "004_district_views.sql"


def resolve_district(cur, value: str) -> tuple[int, str]:
    if value.isdigit():
        cur.execute("SELECT osm_id, name FROM districts WHERE osm_id = %s", (int(value),))
    else:
        cur.execute(
            """
            SELECT osm_id, name
            FROM districts
            WHERE lower(name) IN (lower(%(v)s), lower(%(v)s) || ' район')
               OR name ILIKE '%%' || %(v)s || '%%'
            ORDER BY (lower(name) IN (lower(%(v)s), lower(%(v)s) || ' район')) DESC,
                     length(name), osm_id
            LIMIT 1
            """,
            {"v": value},
        )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"Район не найден: {value}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--district", required=True, help="osm_id района или имя")
    parser.add_argument("--out", type=Path, default=None, help="каталог результата")
    parser.add_argument("--no-tiles", action="store_true", help="не собирать PMTiles")
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="не пересчитывать scores, выгрузить текущие значения из БД",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    shop_subtypes = cfg["bonuses"]["categories"]["shops"].get("subtypes", [])

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
        conn.commit()
        d_id, d_name = resolve_district(cur, args.district)

    out_dir = (args.out or ROOT / "data" / "districts" / str(d_id)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Район: {d_name} (osm_id={d_id}) -> {out_dir}")

    # Балл — только жилым. district_buildings возвращает все здания района,
    # поэтому фильтр is_home ставим в подзапросе скоупа.
    if args.no_score:
        print("Скоринг: пропуск (--no-score), берём текущие scores из БД")
    else:
        homes_scored = scoring.run(
            "(SELECT * FROM district_buildings(%s) WHERE is_home)", [d_id], cfg
        )
        print(f"Жилых со баллом: {homes_scored:,}")

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        data = export.collect(
            cur,
            shop_subtypes,
            buildings="district_buildings(%s)",
            buildings_params=(d_id,),
            poi="district_poi(%s)",
            poi_params=(d_id,),
            linear="district_linear(%s)",
            linear_params=(d_id,),
            district_geom="SELECT geometry FROM districts WHERE osm_id = %s",
            district_geom_params=(d_id,),
            include_routes=False,
        )
        cur.execute(
            "SELECT ST_AsGeoJSON(geometry) FROM districts WHERE osm_id = %s", (d_id,)
        )
        district_geom = json.loads(cur.fetchone()[0])

    export.export_outputs(
        out_dir,
        data,
        buildings_name="buildings.geojson",
        poi_name="poi.geojson",
        redzones_name="redzones.geojson",
    )
    export.write_geojson(
        out_dir / "district.geojson",
        [
            {
                "type": "Feature",
                "properties": {"id": d_id, "name": d_name},
                "geometry": district_geom,
            }
        ],
    )

    home_features = [
        f
        for f in data["buildings"]
        if f["properties"]["is_home"] and f["properties"]["index"] is not None
    ]
    index_rows = build_index(home_features)
    index_path = out_dir / "buildings-index.json"
    index_path.write_text(
        json.dumps(index_rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    tiles_stats = None
    tiles_path = None
    if not args.no_tiles:
        staged = out_dir / ".buildings.ndjson"
        tiles_path = out_dir / "buildings.pmtiles"
        try:
            write_ndjson(data["buildings"], staged)
            run_tippecanoe(staged, tiles_path)
        finally:
            staged.unlink(missing_ok=True)
        tiles_stats = pmtiles_stats(tiles_path)

    report = {
        "district": {"osm_id": d_id, "name": d_name},
        "buildings": len(data["buildings"]),
        "homes": len(home_features),
        "poi": len(data["poi"]),
        "redzones": len(data["redzones"]),
        "geojson_bytes": {
            "buildings": (out_dir / "buildings.geojson").stat().st_size,
            "poi": (out_dir / "poi.geojson").stat().st_size,
            "redzones": (out_dir / "redzones.geojson").stat().st_size,
            "district": (out_dir / "district.geojson").stat().st_size,
        },
        "index_rows": len(index_rows),
        "index_bytes": index_path.stat().st_size,
        "tiles": None
        if tiles_stats is None
        else {
            "bytes": tiles_path.stat().st_size,
            "count": tiles_stats["tiles"],
        },
        "seconds": round(time.perf_counter() - started, 1),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"Зданий: {report['buildings']:,}, жилых: {report['homes']:,}, "
        f"POI: {report['poi']:,}, redzones: {report['redzones']:,}"
    )
    print(f"index: {report['index_rows']:,} строк, {human_size(report['index_bytes'])}")
    if report["tiles"]:
        print(
            f"tiles: {report['tiles']['count']:,} тайлов, "
            f"{human_size(report['tiles']['bytes'])}"
        )
    print(f"Готово за {report['seconds']} с: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
