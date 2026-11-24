#!/usr/bin/env python3
"""Оркестратор прогона Москвы целиком (V2-S3b).

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/run_city.py --stage boundaries
  pipeline/.venv/Scripts/python.exe pipeline/run_city.py --stage all --dry-run
  pipeline/.venv/Scripts/python.exe pipeline/run_city.py --stage export --districts all
  pipeline/.venv/Scripts/python.exe pipeline/run_city.py --stage routes --districts 1275551,1257786

Этапы (по порядку):
  boundaries — граница города admin_level=4 в таблицу moscow, вьюха
               moscow_districts (районы внутри), применение overrides;
  export     — здания/POI/красные зоны/индекс района в data/districts/<id>/
               через build_district.py (без per-district PMTiles: они больше
               не нужны, городские тайлы собирает этап tiles);
  routes     — пешие маршруты только жилым (routes_valhalla.py --district);
  iso        — изохроны 5/10/15 только жилым (isochrones_valhalla.py);
  pack       — шарды 16+16, data/index.json.gz, data/districts/manifest.json;
  tiles      — ОДИН городской data/city/buildings.pmtiles и
               data/city/basemap.pmtiles (не 132 файла).

Резюмируемость: готовый район/файл не пересчитывается. export пропускает
район с summary.json, pack — район с полными шардами, tiles — готовые
файлы; маршруты и изохроны резюмируются сами (таблица routes и файлы).
Отказы по id пишутся в data/city/<stage>-errors.json и не блокируют
остальные районы; итоговый код 1, если что-то упало.

--dry-run только печатает план (районы, здания, жилые, маршруты, изохроны,
оценка часов и размера) и ничего не пишет — ни в БД, ни на диск.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import buildings_filter  # noqa: E402
import extract_moscow  # noqa: E402
from db import DB_DSN  # noqa: E402

DISTRICTS_DIR = ROOT / "data" / "districts"
CITY_DIR = ROOT / "data" / "city"
INDEX = ROOT / "data" / "index.json.gz"
PBF = Path("data/raw/moscow.osm.pbf")  # относительный: pyosmium не любит кириллицу

STAGES = ("boundaries", "export", "routes", "iso", "pack", "tiles")
SHARDS = 16

# Оценки для --dry-run. Числа — из фактических замеров, а не «на глаз»:
# 2b: 2 545 маршрутных запросов за 55 с; замер 23.11: 96 за 1,7 с (56 req/s),
# изохроны 24 за 0,2 с (113 req/s). Берём консервативно.
ROUTE_REQ_PER_SEC = 46.0
ISO_REQ_PER_SEC = 80.0
ROUTE_SHARD_BYTES = 700  # gzip на дом: 2,44 МБ / 3 585 домов трёх районов
ISO_SHARD_BYTES = 275  # gzip на дом: 0,96 МБ / 3 585 домов
INDEX_BYTES_PER_HOME = 15  # 51 КБ на 3 585 домов
BUILDINGS_TILE_BYTES = 990  # Пресня: 3,2 МБ / 3 306 зданий (с breakdown)
BASEMAP_BYTES_PER_ROAD_KM = 795 * 1024 / 57  # Пресня: 795 КБ / 57 км улиц
EXPORT_SEC_PER_BUILDING = 0.012  # 2b: 6,8–20,7 мс/здание (Пресня 22 с, Тверской 67 с)
PACK_SEC_PER_HOME = 0.002
TILES_SECONDS = 15 * 60
BOUNDARIES_SECONDS = 300


def human(value: float) -> str:
    if value >= 1 << 30:
        return f"{value / (1 << 30):.2f} ГиБ"
    if value >= 1 << 20:
        return f"{value / (1 << 20):.1f} МиБ"
    return f"{value / 1024:.0f} КиБ"


def hours(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} мин"
    return f"{seconds / 3600:.1f} ч"


def connect():
    import psycopg

    return psycopg.connect(DB_DSN)


# --------------------------------------------------------------------------- #
# районы и план
# --------------------------------------------------------------------------- #

def resolve_districts(cur, value: str) -> list[int]:
    """all — районы внутри границы Москвы; иначе список osm_id через запятую."""
    if value.strip().lower() == "all":
        try:
            cur.execute("SELECT osm_id FROM moscow_districts ORDER BY osm_id")
        except Exception as exc:  # вьюхи нет — сначала этап boundaries
            raise SystemExit(
                "Нет вьюхи moscow_districts: сначала запустите --stage boundaries "
                f"({exc})"
            ) from exc
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            raise SystemExit("moscow_districts пуста: проверьте границу Москвы")
        return ids

    ids = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            raise SystemExit(f"Ожидается osm_id района, получено: {chunk!r}")
        ids.append(int(chunk))
    if not ids:
        raise SystemExit("--districts пуст: all или id,id")
    cur.execute("SELECT osm_id FROM districts WHERE osm_id = ANY(%s)", (ids,))
    known = {row[0] for row in cur.fetchall()}
    missing = [d for d in ids if d not in known]
    if missing:
        raise SystemExit(f"Районы не найдены в districts: {missing}")
    return ids


def plan_rows(cur, district_ids: list[int]) -> list[dict]:
    cur.execute(
        """
        SELECT d.osm_id, d.name,
               count(b.osm_id) AS buildings,
               count(*) FILTER (WHERE b.is_home) AS homes
        FROM districts d
        JOIN buildings b ON ST_Intersects(b.geometry, d.geometry)
        WHERE d.osm_id = ANY(%s)
        GROUP BY d.osm_id, d.name
        ORDER BY homes DESC, d.name
        """,
        (district_ids,),
    )
    return [
        {"id": row[0], "name": row[1], "buildings": row[2], "homes": row[3]}
        for row in cur.fetchall()
    ]


def unique_homes(cur, district_ids: list[int]) -> int:
    cur.execute(
        """
        SELECT count(DISTINCT b.osm_id)
        FROM buildings b
        JOIN districts d ON d.osm_id = ANY(%s)
        WHERE b.is_home AND ST_Intersects(b.geometry, d.geometry)
        """,
        (district_ids,),
    )
    return cur.fetchone()[0]


def categories() -> dict:
    cfg = json.loads((ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))
    return cfg["bonuses"]["categories"]


def road_km(cur, district_ids: list[int]) -> float:
    cur.execute(
        """
        SELECT coalesce(sum(ST_Length(l.geometry::geography)), 0) / 1000.0
        FROM linear l
        JOIN districts d ON d.osm_id = ANY(%s)
        WHERE l.kind = 'road' AND ST_Intersects(l.geometry, d.geometry)
        """,
        (district_ids,),
    )
    return float(cur.fetchone()[0])


def print_plan(
    rows: list[dict], unique: int, stages: list[str], all_districts: bool, roads_km: float
) -> None:
    from routes_valhalla import CANDIDATES

    cats = categories()
    homes = sum(row["homes"] for row in rows)
    buildings = sum(row["buildings"] for row in rows)
    routes = homes * len(cats)
    requests = routes * CANDIDATES
    route_seconds = requests / ROUTE_REQ_PER_SEC
    iso_seconds = homes / ISO_REQ_PER_SEC
    export_seconds = buildings * EXPORT_SEC_PER_BUILDING
    pack_seconds = homes * PACK_SEC_PER_HOME
    route_bytes = homes * ROUTE_SHARD_BYTES
    iso_bytes = homes * ISO_SHARD_BYTES
    index_bytes = homes * INDEX_BYTES_PER_HOME
    buildings_tiles = buildings * BUILDINGS_TILE_BYTES
    basemap_tiles = roads_km * BASEMAP_BYTES_PER_ROAD_KM

    print("План прогона (dry-run: ничего не пишется)")
    print(
        f"  районов: {len(rows)}, зданий: {buildings:,}, "
        f"жилых: {homes:,} (уникальных id: {unique:,})"
    )
    print(f"  этапы: {', '.join(stages)}")
    if all_districts and len(rows) != 132:
        print(f"  ВНИМАНИЕ: районов {len(rows)}, а в Москве по закону 132 — стоп-условие")
    if "boundaries" in stages:
        print(f"  boundaries: PBF-скан ~{BOUNDARIES_SECONDS // 60} мин + overrides")
    if "export" in stages:
        print(
            f"  export: {len(rows)} районов, ~{hours(export_seconds)} "
            f"(12 мс/здание), без per-district PMTiles"
        )
    if "routes" in stages:
        print(
            f"  routes: {routes:,} маршрутов ({len(cats)} категорий × жилые), "
            f"{requests:,} запросов Valhalla (×{CANDIDATES} кандидата), "
            f"~{hours(route_seconds)} при {ROUTE_REQ_PER_SEC:.0f} запр/с"
        )
    if "iso" in stages:
        print(
            f"  iso: {homes:,} изохрон (3 контура), "
            f"~{hours(iso_seconds)} при {ISO_REQ_PER_SEC:.0f} запр/с"
        )
    if "pack" in stages:
        print(
            f"  pack: {len(rows) * SHARDS * 2:,} шардов, "
            f"{human(route_bytes)} маршруты + {human(iso_bytes)} изохроны (gzip), "
            f"индекс {human(index_bytes)}, ~{hours(pack_seconds)}"
        )
    if "tiles" in stages:
        print(
            f"  tiles: 1 buildings.pmtiles ~{human(buildings_tiles)} "
            f"(верхняя оценка: {buildings:,} × 990 Б) + 1 basemap.pmtiles "
            f"~{human(basemap_tiles)} ({roads_km:.0f} км улиц)"
        )
    total_seconds = 0.0
    if "boundaries" in stages:
        total_seconds += BOUNDARIES_SECONDS
    if "export" in stages:
        total_seconds += export_seconds
    if "routes" in stages:
        total_seconds += route_seconds
    if "iso" in stages:
        total_seconds += iso_seconds
    if "pack" in stages:
        total_seconds += pack_seconds
    if "tiles" in stages:
        total_seconds += TILES_SECONDS
    disk = 0.0
    if "routes" in stages:
        disk += route_bytes
    if "iso" in stages:
        disk += iso_bytes
    if "pack" in stages:
        disk += index_bytes
    if "tiles" in stages:
        disk += buildings_tiles + basemap_tiles
    print(f"  всего: ~{hours(total_seconds)} CPU/сети, ~{human(disk)} на диске")
    top = rows[:5]
    bottom = rows[-5:][::-1] if len(rows) > 5 else rows[::-1]
    print("  крупнейшие по жилым: " + ", ".join(f"{r['name']} {r['homes']:,}" for r in top))
    print("  наименьшие по жилым: " + ", ".join(f"{r['name']} {r['homes']:,}" for r in bottom))
    print("  запуск без --dry-run выполнит перечисленные этапы по порядку.")


# --------------------------------------------------------------------------- #
# этапы
# --------------------------------------------------------------------------- #

def run_script(script: str, *args: str) -> int:
    cmd = [sys.executable, str(ROOT / "pipeline" / script), *args]
    print(f"  $ {script} {' '.join(args)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def stage_boundaries() -> list[dict]:
    started = time.monotonic()
    rc = extract_moscow.main()
    if rc != 0:
        return [{"stage": "boundaries", "error": f"extract_moscow вернул {rc}"}]
    with connect() as conn, conn.cursor() as cur:
        changed = buildings_filter.apply_overrides(cur)
        conn.commit()
        for osm_id, new, old in changed:
            print(f"  override {osm_id}: is_home {old} -> {new}")
        cur.execute("SELECT count(*) FROM moscow_districts")
        districts = cur.fetchone()[0]
    print(
        f"  границы: районов в Москве {districts}, "
        f"overrides изменено {len(changed)}, {time.monotonic() - started:.0f} с"
    )
    return []


def district_dir(d_id: int) -> Path:
    return DISTRICTS_DIR / str(d_id)


def export_done(d_id: int) -> bool:
    path = district_dir(d_id)
    return (path / "summary.json").exists() and (path / "buildings.geojson").exists()


def shards_done(d_id: int) -> bool:
    for root in (ROOT / "data" / "routes", ROOT / "data" / "isochrones"):
        for shard in range(SHARDS):
            if not (root / str(d_id) / f"{shard}.json.gz").exists():
                return False
    return True


def stage_export(rows: list[dict], force: bool) -> list[dict]:
    errors: list[dict] = []
    for row in rows:
        d_id = row["id"]
        if export_done(d_id) and not force:
            print(f"  {d_id} {row['name']}: готово, пропуск")
            continue
        started = time.monotonic()
        rc = run_script("build_district.py", "--district", str(d_id), "--no-tiles")
        status = "ok" if rc == 0 else f"ошибка {rc}"
        print(f"  {d_id} {row['name']}: {status}, {time.monotonic() - started:.0f} с")
        if rc != 0:
            errors.append({"district": d_id, "name": row["name"], "code": rc})
    return errors


def stage_routes(rows: list[dict]) -> list[dict]:
    CITY_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []
    for row in rows:
        d_id = row["id"]
        log = CITY_DIR / f"routes-errors-{d_id}.json"
        log.unlink(missing_ok=True)  # иначе можно прочитать вчерашний журнал
        started = time.monotonic()
        rc = run_script(
            "routes_valhalla.py", "--district", str(d_id), "--errors-log", str(log)
        )
        failed = json.loads(log.read_text(encoding="utf-8"))["errors"] if log.exists() else []
        print(
            f"  {d_id} {row['name']}: {'ok' if rc == 0 else f'ошибка {rc}'}, "
            f"отказов {len(failed)}, {time.monotonic() - started:.0f} с"
        )
        if rc != 0 or failed:
            errors.append(
                {"district": d_id, "name": row["name"], "code": rc, "failed": failed}
            )
    return errors


def stage_iso(rows: list[dict]) -> list[dict]:
    CITY_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []
    for row in rows:
        d_id = row["id"]
        log = CITY_DIR / f"iso-errors-{d_id}.json"
        log.unlink(missing_ok=True)
        started = time.monotonic()
        rc = run_script(
            "isochrones_valhalla.py", "--district", str(d_id), "--errors-log", str(log)
        )
        failed = json.loads(log.read_text(encoding="utf-8"))["errors"] if log.exists() else []
        print(
            f"  {d_id} {row['name']}: {'ok' if rc == 0 else f'ошибка {rc}'}, "
            f"отказов {len(failed)}, {time.monotonic() - started:.0f} с"
        )
        if rc != 0 or failed:
            errors.append(
                {"district": d_id, "name": row["name"], "code": rc, "failed": failed}
            )
    return errors


def stage_pack(rows: list[dict], force: bool) -> list[dict]:
    errors: list[dict] = []
    for row in rows:
        d_id = row["id"]
        if shards_done(d_id) and not force:
            print(f"  {d_id} {row['name']}: шарды готовы, пропуск")
            continue
        rc = run_script("pack_district.py", "--district", str(d_id))
        if rc != 0:
            errors.append({"district": d_id, "name": row["name"], "code": rc})
    for script in ("build_index.py", "build_manifest.py"):
        rc = run_script(script)
        if rc != 0:
            errors.append({"stage": script, "code": rc})
    return errors


def stage_tiles(force: bool) -> list[dict]:
    """Один городской buildings.pmtiles и один basemap.pmtiles."""
    import build_basemap
    import build_tiles

    errors: list[dict] = []
    CITY_DIR.mkdir(parents=True, exist_ok=True)
    buildings_out = CITY_DIR / "buildings.pmtiles"
    basemap_out = CITY_DIR / "basemap.pmtiles"

    if not buildings_out.exists() or force:
        features: dict[int, dict] = {}
        for summary_path in sorted(DISTRICTS_DIR.glob("*/summary.json")):
            if not summary_path.parent.name.isdigit():
                continue  # рабочие копии вроде 1275551_fix — не районы
            path = summary_path.parent / "buildings.geojson"
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for feature in payload["features"]:
                # Дом на границе районов есть в двух geojson — в тайлы один раз.
                features[int(feature["properties"]["id"])] = feature
        if not features:
            errors.append({"stage": "tiles", "error": "нет buildings.geojson в data/districts"})
            return errors
        print(f"  buildings.pmtiles: {len(features):,} зданий (уникальных id)")
        staged = CITY_DIR / ".buildings.ndjson"
        try:
            build_tiles.write_ndjson(list(features.values()), staged)
            build_tiles.run_tippecanoe(staged, buildings_out)
        finally:
            staged.unlink(missing_ok=True)
        print(
            f"  buildings.pmtiles: {human(buildings_out.stat().st_size)}, "
            f"{build_tiles.pmtiles_stats(buildings_out)['tiles']:,} тайлов"
        )
    else:
        print("  buildings.pmtiles: готов, пропуск")

    if not basemap_out.exists() or force:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g) "
                "FROM (SELECT ST_Envelope(geometry) AS g FROM moscow) t"
            )
            min_lon, min_lat, max_lon, max_lat = (float(v) for v in cur.fetchone())
        clip = build_basemap.box(min_lon, min_lat, max_lon, max_lat)
        print(
            f"  basemap: рамка {min_lon:.3f},{min_lat:.3f},{max_lon:.3f},{max_lat:.3f}"
        )
        nodes = build_basemap.NodeCollector(
            (
                min_lon - build_basemap.NODE_MARGIN,
                min_lat - build_basemap.NODE_MARGIN,
                max_lon + build_basemap.NODE_MARGIN,
                max_lat + build_basemap.NODE_MARGIN,
            )
        )
        nodes.apply_file(str(PBF))
        ways = build_basemap.WayCollector(nodes.coords, clip)
        ways.apply_file(str(PBF))
        try:
            ways.features.extend(
                build_basemap.river_features(
                    ROOT / "data" / "raw" / "moskva-river-389341.json", clip
                )
            )
        except Exception as exc:  # сеть/файл — подложка без реки, не блокируем
            print(f"  basemap: предупреждение о реке ({exc})")
        staged = CITY_DIR / ".basemap.ndjson"
        try:
            build_basemap.write_basemap_ndjson(ways.features, staged)
            build_basemap.run_tippecanoe_basemap(staged, basemap_out)
        finally:
            staged.unlink(missing_ok=True)
        print(f"  basemap.pmtiles: {human(basemap_out.stat().st_size)}, "
              f"{len(ways.features):,} объектов")
    else:
        print("  basemap.pmtiles: готов, пропуск")
    return errors


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="all", choices=(*STAGES, "all"))
    parser.add_argument("--districts", default="all", help="all или osm_id через запятую")
    parser.add_argument("--dry-run", action="store_true", help="только план, без записи")
    parser.add_argument("--force", action="store_true", help="пересчитать готовое")
    args = parser.parse_args(argv)

    os.chdir(ROOT)  # относительные пути PBF и pyosmium: кириллица в абсолютном пути
    started = time.monotonic()
    stages = [args.stage] if args.stage != "all" else list(STAGES)

    with connect() as conn, conn.cursor() as cur:
        district_ids = resolve_districts(cur, args.districts)
        rows = plan_rows(cur, district_ids)
        unique = unique_homes(cur, district_ids)
        roads_km = road_km(cur, district_ids)

    if args.dry_run:
        print_plan(
            rows, unique, stages,
            all_districts=args.districts.strip().lower() == "all",
            roads_km=roads_km,
        )
        return 0

    print(
        f"Прогон: этапы {', '.join(stages)}, районов {len(rows)}, "
        f"жилых {unique:,}"
    )
    errors: list[dict] = []
    for stage in stages:
        print(f"\n== {stage} ==", flush=True)
        if stage == "boundaries":
            errors += stage_boundaries()
        elif stage == "export":
            errors += stage_export(rows, args.force)
        elif stage == "routes":
            errors += stage_routes(rows)
        elif stage == "iso":
            errors += stage_iso(rows)
        elif stage == "pack":
            errors += stage_pack(rows, args.force)
        elif stage == "tiles":
            errors += stage_tiles(args.force)
        if errors:
            CITY_DIR.mkdir(parents=True, exist_ok=True)
            (CITY_DIR / f"{stage}-errors.json").write_text(
                json.dumps({"stage": stage, "errors": errors}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  отказы записаны: data/city/{stage}-errors.json")

    print(f"\nГотово за {time.monotonic() - started:.0f} с, отказов: {len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
