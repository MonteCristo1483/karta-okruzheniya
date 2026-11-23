#!/usr/bin/env python3
"""Единственное правило `is_home`: жилой дом для расчёта или нет.

Логика из config/buildings.json, без копий списков в других модулях:
  * по умолчанию жилое, если есть addr:street и addr:housenumber
    (require_address / default_is_home);
  * понижаем до нежилого только по явному признаку — значению building=*
    из non_home_building или паре ключ/значение из non_home_tags;
  * порога этажности нет: иначе вырежется частный сектор Некрасовки.

Всё, что не жилое, остаётся на карте, но не получает балл, маршруты и
изохрону. Импорт, экспорт, скоринг и выборка маршрутов вызывают только
`is_home()` отсюда.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "buildings.json"


@lru_cache(maxsize=1)
def _rules() -> tuple[bool, bool, frozenset[str], tuple[tuple[str, frozenset[str]], ...]]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    building = frozenset(cfg.get("non_home_building", []))
    tags = tuple(
        (key, frozenset(values))
        for key, values in cfg.get("non_home_tags", {}).items()
    )
    return (
        bool(cfg.get("require_address", True)),
        bool(cfg.get("default_is_home", True)),
        building,
        tags,
    )


def is_home(tags: Mapping[str, str] | None) -> bool:
    """True — жилой дом (получает балл, маршруты, изохрону); False — нет."""
    if not tags:
        return False
    require_address, default_home, non_home_building, non_home_tags = _rules()
    if require_address and not (
        tags.get("addr:street") and tags.get("addr:housenumber")
    ):
        return False
    if tags.get("building") in non_home_building:
        return False
    for key, values in non_home_tags:
        if tags.get(key) in values:
            return False
    return default_home
