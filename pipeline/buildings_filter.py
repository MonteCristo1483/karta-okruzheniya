#!/usr/bin/env python3
"""Единственное правило `is_home`: жилой дом для расчёта или нет.

Логика из config/buildings.json, без копий списков в других модулях:
  * явные нежилые признаки сильнее всего: building=* из non_home_building
    или пара ключ/значение из non_home_tags -> нежилое;
  * жилой тип застройки (home_building: apartments, residential, house,
    detached, semidetached_house, terrace, dormitory) -> жилое даже без
    адреса на полигоне;
  * addr:housenumber на полигоне -> жилое даже без addr:street
    (в Зеленограде улица приходит из addr:place, в остальных районах —
    от соседнего дома в 30 м или из адресного реестра);
  * иначе, если require_address=true — нежилое; если false — значение
    default_is_home. Порога этажности нет: иначе вырежется частный сектор
    Некрасовки.

Последними применяются ручные исключения из config/buildings_overrides.json
(формат: {"v":1,"overrides":[{"osm_id":123,"is_home":true,"reason":"..."}]}):
они перекрывают и теги, и адресное правило, в обе стороны. Запись без
непустого reason считается ошибкой конфигурации — факт должен быть
подтверждён источником, а не угадан.

Связь с жилым паспортом ГИС ЖКХ (building_year_match.distance_m <= 25)
проверяется не здесь, а в PostGIS при переклассификации
(pipeline/reclassify_homes.py): функция остаётся чистой от БД.

Всё, что не жилое, остаётся на карте, но не получает балл, маршруты и
изохрону. Импорт, экспорт, скоринг и выборка маршрутов вызывают только
`is_home()` отсюда.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "buildings.json"
OVERRIDES_PATH = ROOT / "config" / "buildings_overrides.json"


@lru_cache(maxsize=1)
def _rules() -> tuple[
    bool, bool, frozenset[str], frozenset[str], tuple[tuple[str, frozenset[str]], ...]
]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    building = frozenset(cfg.get("non_home_building", []))
    home_building = frozenset(cfg.get("home_building", []))
    tags = tuple(
        (key, frozenset(values))
        for key, values in cfg.get("non_home_tags", {}).items()
    )
    return (
        bool(cfg.get("require_address", True)),
        bool(cfg.get("default_is_home", True)),
        home_building,
        building,
        tags,
    )


def load_overrides(path: Path) -> dict[int, bool]:
    """osm_id -> is_home; пусто, если файла нет. Битый формат — ValueError."""
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("v") != 1:
        raise ValueError(f"{path.name}: ожидается v=1")
    result: dict[int, bool] = {}
    for item in cfg.get("overrides", []):
        osm_id = int(item["osm_id"])
        if osm_id in result:
            raise ValueError(f"{path.name}: повтор osm_id {osm_id}")
        if not isinstance(item.get("is_home"), bool):
            raise ValueError(f"{path.name}: osm_id {osm_id} без bool is_home")
        if not str(item.get("reason", "")).strip():
            raise ValueError(f"{path.name}: osm_id {osm_id} без reason")
        result[osm_id] = item["is_home"]
    return result


@lru_cache(maxsize=1)
def _overrides() -> dict[int, bool]:
    return load_overrides(OVERRIDES_PATH)


def classify(
    tags: Mapping[str, str] | None, osm_id: int | None = None
) -> tuple[bool, bool]:
    """(is_home по тегам, можно ли поднимать по паспорту ГИС ЖКХ).

    Второй флаг False только у явных нежилых признаков и ручных overrides:
    жилой паспорт в 25 м не должен «оживлять» гараж или офис.
    """
    if osm_id is not None:
        override = _overrides().get(int(osm_id))
        if override is not None:
            return override, False
    if not tags:
        return False, True
    require_address, default_home, home_building, non_home_building, non_home_tags = (
        _rules()
    )
    if tags.get("building") in non_home_building:
        return False, False
    for key, values in non_home_tags:
        if tags.get(key) in values:
            return False, False
    if tags.get("building") in home_building:
        return True, True
    if tags.get("addr:housenumber"):
        return True, True
    if not require_address:
        return default_home, True
    return False, True


def is_home(tags: Mapping[str, str] | None, osm_id: int | None = None) -> bool:
    """True — жилой дом (получает балл, маршруты, изохрону); False — нет.

    osm_id нужен только для ручных исключений; без него правило работает
    по тегам. Overrides применяются первыми.
    """
    return classify(tags, osm_id)[0]


def apply_overrides(cursor) -> list[tuple[int, bool, str]]:
    """Пересчитать is_home у строк buildings, названных в overrides.

    Нужно, чтобы уже загруженная база (полный импорт идёт часами) получила
    ручные исключения без повторного импорта. Возвращает список изменений
    (osm_id, новое значение, старое значение); отсутствующие id — в stderr.
    """
    changed: list[tuple[int, bool, str]] = []
    for osm_id, target in sorted(_overrides().items()):
        cursor.execute("SELECT tags, is_home FROM buildings WHERE osm_id = %s", (osm_id,))
        row = cursor.fetchone()
        if row is None:
            print(f"WARNING: override osm_id {osm_id} отсутствует в buildings", file=sys.stderr)
            continue
        tags, current = row
        # is_home считаем по тегам, но с этим же id — так же, как при импорте.
        expected = is_home(tags, osm_id)
        if expected != bool(current):
            cursor.execute(
                "UPDATE buildings SET is_home = %s WHERE osm_id = %s", (expected, osm_id)
            )
            changed.append((osm_id, expected, str(current)))
    return changed


def main(argv: list[str]) -> int:
    if "--apply" in argv:
        import psycopg

        from db import DB_DSN

        with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
            changed = apply_overrides(cur)
            conn.commit()
        for osm_id, new, old in changed:
            print(f"  {osm_id}: is_home {old} -> {new}")
        print(f"Применено overrides: {len(_overrides())}, изменено строк: {len(changed)}")
        return 0

    overrides = _overrides()
    print(f"Overrides: {len(overrides)}")
    for osm_id, value in sorted(overrides.items()):
        print(f"  {osm_id}: is_home={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
