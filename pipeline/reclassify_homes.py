#!/usr/bin/env python3
"""HOMES-V2: переклассификация `is_home` по Москве + адресное обогащение.

Что делает (идемпотентно, без пересборки OSM):

1. Пересчитывает `buildings.is_home` по `buildings_filter.classify(tags, osm_id)`
   (теги + ручные overrides) для всех зданий внутри `moscow`.
2. Поднимает до `is_home = TRUE` здания, у которых есть жилой паспорт ГИС ЖКХ
   в `building_year_match` (`distance_m <= 25`), если они не заблокированы
   явным нежилым тегом или override.
3. Заполняет пустые `addr_street` / `addr_housenumber` у жилых домов Москвы:
   адрес из ближайшей записи `building_years` (≤25 м, адресный реестр /
   ГИС ЖКХ), иначе `addr:place` из тегов, иначе улица у соседнего дома
   с тем же номером в 30 м (та же логика, что в export_map_data). Уже
   заполненные адреса не перетираются.

CLI:
  pipeline/.venv/Scripts/python.exe pipeline/reclassify_homes.py
  pipeline/.venv/Scripts/python.exe pipeline/reclassify_homes.py --dry-run

Снимок 10 контрольных домов (до/после) пишется в
`data/processed/homes_v2_controls.json`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import buildings_filter  # noqa: E402
from db import DB_DSN  # noqa: E402

CONTROLS_PATH = ROOT / "data" / "processed" / "homes_v2_controls.json"
BATCH = 20_000
YEAR_MATCH_RADIUS_M = 25

CREATE_MOSCOW_TMP = """
CREATE TEMP TABLE moscow_buildings AS
SELECT b.osm_id
FROM buildings b
JOIN moscow m ON ST_Intersects(b.geometry, m.geometry)
"""
CREATE_MOSCOW_IDX = "CREATE INDEX ON moscow_buildings (osm_id)"

# Адреса реестра: «124575, г Москва, г Зеленоград, ул Рабочая, д. 19, к. 1».
HOUSE_RE = re.compile(r"^(?:д|дом|влд|вл)\.?\s*(\d.*)$", re.IGNORECASE)
BLOCK_RE = re.compile(r"^(?:к|корп|корпус)\.?\s*(\d+.*)$", re.IGNORECASE)
BUILD_RE = re.compile(r"^(?:стр|строение|с)\.?\s*(\d+.*)$", re.IGNORECASE)
CITY_RE = re.compile(
    r"^(?:г|город|пгт|рп|п|пос|посёлок|поселок|с|село|д|деревня)\.?\s+\S",
    re.IGNORECASE,
)
STREET_TYPES = {
    "ул": "улица", "улица": "улица", "пер": "переулок", "переулок": "переулок",
    "ш": "шоссе", "шоссе": "шоссе", "просп": "проспект", "пр-т": "проспект",
    "пр-кт": "проспект", "проспект": "проспект", "наб": "набережная",
    "набережная": "набережная", "бул": "бульвар", "б-р": "бульвар",
    "бульвар": "бульвар", "пл": "площадь", "площадь": "площадь",
    "туп": "тупик", "тупик": "тупик", "пр-д": "проезд", "проезд": "проезд",
    "аллея": "аллея", "мкр": "микрорайон", "микрорайон": "микрорайон",
}


def parse_registry_address(address: str | None) -> tuple[str | None, str | None]:
    """«124575, г Москва, ул Рабочая, д. 19, к. 1» -> («ул Рабочая», «19 к1»)."""
    if not address:
        return None, None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    house_at = None
    base = suffix = None
    for i, part in enumerate(parts):
        match = HOUSE_RE.match(part)
        if match:
            house_at, base = i, match.group(1).strip()
            for extra in parts[i + 1:]:
                block = BLOCK_RE.match(extra)
                build = BUILD_RE.match(extra)
                if block:
                    suffix = f"к{block.group(1).strip()}"
                    break
                if build:
                    suffix = f"с{build.group(1).strip()}"
                    break
            break
    if house_at is None or base is None:
        return None, None
    street_parts = [
        part
        for part in parts[:house_at]
        if not part.isdigit() and not CITY_RE.match(part)
    ]
    street = ", ".join(street_parts) or None
    housenumber = base if suffix is None else f"{base} {suffix}"
    return street, housenumber


def street_key(name: str | None) -> str:
    """Нормализация улицы для сверки OSM и реестра («Рабочая улица» = «ул Рабочая»)."""
    if not name:
        return ""
    text = name.lower().replace("ё", "е")
    text = re.sub(r"[.,«»\"()]", " ", text)
    words = [word for word in text.split() if word not in {"г", "город", "москва"}]
    while words and words[0] in STREET_TYPES:
        words.pop(0)
    while words and words[-1] in STREET_TYPES:
        words.pop()
    return " ".join(words)


def same_street(left: str | None, right: str | None) -> bool:
    left_key, right_key = street_key(left), street_key(right)
    return bool(left_key) and bool(right_key) and (
        left_key == right_key or left_key in right_key or right_key in left_key
    )


def moscow_buildings(cur) -> int:
    cur.execute(CREATE_MOSCOW_TMP)
    cur.execute(CREATE_MOSCOW_IDX)
    cur.execute("SELECT count(*) FROM moscow_buildings")
    return cur.fetchone()[0]


def year_match_ids(cur) -> set[int]:
    cur.execute(
        "SELECT osm_id FROM building_year_match WHERE distance_m <= %s",
        (YEAR_MATCH_RADIUS_M,),
    )
    return {row[0] for row in cur.fetchall()}


def reclassify(cur, dry_run: bool) -> dict:
    matched = year_match_ids(cur)
    print(f"Паспортов ГИС ЖКХ в {YEAR_MATCH_RADIUS_M} м: {len(matched):,}")
    changes: list[tuple[int, bool, str]] = []
    to_home = to_non_home = from_passport = 0
    last_id = -1
    while True:
        cur.execute(
            """
            SELECT b.osm_id, b.is_home, b.tags
            FROM buildings b
            JOIN moscow_buildings mb ON mb.osm_id = b.osm_id
            WHERE b.osm_id > %s
            ORDER BY b.osm_id
            LIMIT %s
            """,
            (last_id, BATCH),
        )
        rows = cur.fetchall()
        if not rows:
            break
        last_id = rows[-1][0]
        for osm_id, current, tags in rows:
            target, can_use_passport = buildings_filter.classify(tags, osm_id)
            reason = "tags"
            if not target and can_use_passport and osm_id in matched:
                target, reason = True, "passport"
            if bool(current) == target:
                continue
            if target:
                to_home += 1
                if reason == "passport":
                    from_passport += 1
            else:
                to_non_home += 1
            changes.append((osm_id, target, reason))
    print(f"Изменений is_home: {len(changes):,} (в жилые {to_home:,}, из них по паспорту {from_passport:,}; в нежилые {to_non_home:,})")
    if not dry_run and changes:
        cur.executemany(
            "UPDATE buildings SET is_home = %s WHERE osm_id = %s",
            [(target, osm_id) for osm_id, target, _reason in changes],
        )
    return {
        "changed": len(changes),
        "to_home": to_home,
        "to_non_home": to_non_home,
        "from_passport": from_passport,
    }


ADDRESS_SOURCES = """
SELECT b.osm_id,
       b.addr_street,
       b.addr_housenumber,
       b.tags->>'addr:place' AS place,
       y.address AS registry_address
FROM buildings b
JOIN moscow_buildings mb ON mb.osm_id = b.osm_id
LEFT JOIN LATERAL (
    SELECT yb.address
    FROM building_years yb
    WHERE yb.address IS NOT NULL AND btrim(yb.address) <> ''
      AND yb.geom && ST_Expand(ST_PointOnSurface(b.geometry), 0.0005)
      AND ST_DWithin(
            ST_PointOnSurface(b.geometry)::geography, yb.geom::geography, 25)
    ORDER BY yb.geom <-> ST_PointOnSurface(b.geometry)
    LIMIT 1
) y ON true
WHERE b.is_home
  AND (coalesce(b.addr_street, '') = '' OR coalesce(b.addr_housenumber, '') = '')
"""


def fill_addresses_from_sources(cur, dry_run: bool) -> int:
    """Улица/номер из реестра или addr:place. Возвращает число обновлённых строк."""
    cur.execute(ADDRESS_SOURCES)
    rows = cur.fetchall()
    updates: list[tuple[str, str, int]] = []
    for osm_id, street, housenumber, place, registry_address in rows:
        reg_street, reg_house = parse_registry_address(registry_address)
        new_street = street or None
        new_house = housenumber or None
        if not new_street:
            if reg_street:
                new_street = reg_street
            elif place:
                new_street = place.strip() or None
        if not new_house and reg_house:
            # Номер из реестра берём только когда улица подтверждает пару:
            # иначе есть риск приписать дому номер соседнего корпуса.
            existing = street or new_street
            if same_street(existing, reg_street):
                new_house = reg_house
        if new_street == (street or None) and new_house == (housenumber or None):
            continue
        updates.append((new_street or "", new_house or "", osm_id))
    print(f"Адреса из реестра/addr:place: обновлений {len(updates):,} из {len(rows):,} кандидатов")
    if not dry_run and updates:
        cur.executemany(
            "UPDATE buildings SET addr_street = %s, addr_housenumber = %s WHERE osm_id = %s",
            updates,
        )
    return len(updates)


NEIGHBOUR_STREET = """
UPDATE buildings b
SET addr_street = n.street
FROM (
    SELECT b2.osm_id,
           (
               SELECT nb.addr_street
               FROM buildings nb
               WHERE nb.addr_street IS NOT NULL AND nb.addr_street <> ''
                 AND nb.addr_housenumber IS NOT NULL
                 AND split_part(nb.addr_housenumber, ' ', 1)
                     = split_part(b2.addr_housenumber, ' ', 1)
                 AND nb.geometry && ST_Expand(b2.geometry, 0.0006)
                 AND ST_DWithin(nb.geometry, b2.geometry, 0.0006)
                 AND ST_DWithin(nb.geometry::geography, b2.geometry::geography, 30)
               ORDER BY nb.geometry <-> b2.geometry
               LIMIT 1
           ) AS street
    FROM buildings b2
    JOIN moscow_buildings mb ON mb.osm_id = b2.osm_id
    WHERE b2.is_home
      AND (b2.addr_street IS NULL OR b2.addr_street = '')
      AND b2.addr_housenumber IS NOT NULL AND b2.addr_housenumber <> ''
) n
WHERE b.osm_id = n.osm_id AND n.street IS NOT NULL
"""


def fill_neighbour_streets(cur, dry_run: bool) -> int:
    if dry_run:
        return 0
    cur.execute(NEIGHBOUR_STREET)
    return cur.rowcount


CONTROL_QUERIES = (
    ("addr:place", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE b.tags->>'addr:place' <> '' AND coalesce(b.addr_street,'')=''
          AND b.tags->>'building' = 'house'
        ORDER BY b.osm_id LIMIT 2"""),
    ("addr:housenumber", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE b.tags->>'addr:housenumber' <> '' AND coalesce(b.addr_street,'')=''
        ORDER BY b.osm_id LIMIT 2"""),
    ("residential+паспорт", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE NOT b.is_home AND b.tags->>'building' IN ('apartments','residential')
          AND NOT (b.tags ? 'addr:housenumber')
          AND EXISTS (SELECT 1 FROM building_year_match y
                      WHERE y.osm_id=b.osm_id AND y.distance_m<=25)
        ORDER BY b.osm_id LIMIT 2"""),
    ("detached без адреса", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE NOT b.is_home AND b.tags->>'building'='detached'
          AND NOT (b.tags ? 'addr:housenumber')
          AND NOT EXISTS (SELECT 1 FROM building_year_match y
                          WHERE y.osm_id=b.osm_id AND y.distance_m<=25)
        ORDER BY b.osm_id LIMIT 1"""),
    ("building=yes+паспорт", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE NOT b.is_home AND b.tags->>'building'='yes'
          AND NOT (b.tags ? 'addr:street') AND NOT (b.tags ? 'addr:housenumber')
          AND EXISTS (SELECT 1 FROM building_year_match y
                      WHERE y.osm_id=b.osm_id AND y.distance_m<=25)
        ORDER BY b.osm_id LIMIT 1"""),
    ("dormitory", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE b.tags->>'building'='dormitory' ORDER BY b.osm_id LIMIT 1"""),
    ("нежилой контроль", """
        SELECT b.osm_id FROM buildings b JOIN moscow_buildings mb ON mb.osm_id=b.osm_id
        WHERE b.tags->>'building'='garage' AND b.tags ? 'addr:housenumber'
        ORDER BY b.osm_id LIMIT 1"""),
)


def pick_control_ids(cur) -> dict[int, str]:
    """10 контрольных домов: по одной ветке нового правила (до переклассификации)."""
    ids: dict[int, str] = {}
    for branch, query in CONTROL_QUERIES:
        cur.execute(query)
        for (osm_id,) in cur.fetchall():
            ids.setdefault(int(osm_id), branch)
    return ids


def snapshot_controls(cur, ids: dict[int, str]) -> dict[int, dict]:
    """Состояние контрольных домов: id -> {branch, is_home, index, address}."""
    cur.execute(
        """
        SELECT b.osm_id,
               b.is_home,
               s.index,
               coalesce(b.addr_street, ''),
               coalesce(b.addr_housenumber, '')
        FROM buildings b
        LEFT JOIN scores s ON s.building_id = b.osm_id
        WHERE b.osm_id = ANY(%s)
        """,
        (list(ids),),
    )
    result: dict[int, dict] = {}
    for osm_id, is_home, index, street, house in cur.fetchall():
        result[int(osm_id)] = {
            "branch": ids[int(osm_id)],
            "is_home": bool(is_home),
            "index": None if index is None else float(index),
            "address": ", ".join(p for p in (street, house) if p) or None,
        }
    return result


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="только посчитать изменения")
    args = parser.parse_args(argv)

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        total = moscow_buildings(cur)
        cur.execute(
            "SELECT count(*) FROM buildings b JOIN moscow_buildings mb "
            "ON mb.osm_id=b.osm_id WHERE b.is_home"
        )
        homes_before = cur.fetchone()[0]
        print(f"Зданий Москвы: {total:,}, жилых до: {homes_before:,}")

        controls_before_ids = pick_control_ids(cur)
        controls_before = snapshot_controls(cur, controls_before_ids)
        stats = reclassify(cur, args.dry_run)
        filled = fill_addresses_from_sources(cur, args.dry_run)
        neighbours = fill_neighbour_streets(cur, args.dry_run)
        controls_after = snapshot_controls(cur, controls_before_ids)

        cur.execute(
            "SELECT count(*) FROM buildings b JOIN moscow_buildings mb "
            "ON mb.osm_id=b.osm_id WHERE b.is_home AND coalesce(b.addr_street,'')<>''"
        )
        with_street = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM buildings b JOIN moscow_buildings mb "
            "ON mb.osm_id=b.osm_id WHERE b.is_home"
        )
        homes_after = cur.fetchone()[0]
        if not args.dry_run:
            conn.commit()

    print(
        f"Жилых после: {homes_after:,} (+{homes_after - homes_before:,}); "
        f"с улицей {with_street:,} ({100 * with_street / max(1, homes_after):.1f}%)"
    )
    print(
        f"Адреса: из источников {filled:,}, улица от соседа {neighbours:,}"
    )

    payload = {
        "before": {str(k): v for k, v in controls_before.items()},
        "after": {str(k): v for k, v in controls_after.items()},
        "stats": stats,
        "homes_before": homes_before,
        "homes_after": homes_after,
    }
    CONTROLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTROLS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Снимок контрольных домов: {CONTROLS_PATH.relative_to(ROOT)}")
    for osm_id, after in controls_after.items():
        before = controls_before.get(osm_id, {})
        print(
            f"  {osm_id} [{after['branch']}]: is_home "
            f"{before.get('is_home')} -> {after['is_home']}, "
            f"index {before.get('index')} -> {after['index']}, "
            f"address {before.get('address')!r} -> {after['address']!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
