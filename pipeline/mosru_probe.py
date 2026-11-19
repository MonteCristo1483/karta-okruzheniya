#!/usr/bin/env python3
"""Пробник набора данных портала Москвы: структура, счётчик, пример.

Нужен ключ `MOSRU_API_KEY` в `.env` (см. `.env.example`).

Использование:
  python pipeline/mosru_probe.py 62329
  python pipeline/mosru_probe.py 62329 --rows 2
  python pipeline/mosru_probe.py 62329 --features
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mosru_api import (  # noqa: E402
    MosRuError,
    column_names,
    dataset_count,
    dataset_info,
    features,
    rows,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Пробник набора data.mos.ru")
    parser.add_argument("dataset_id", type=int)
    parser.add_argument("--rows", type=int, default=0,
                        help="показать Cells первых N записей")
    parser.add_argument("--features", action="store_true",
                        help="показать пример геоданных вместо строк")
    args = parser.parse_args(argv)

    try:
        info = dataset_info(args.dataset_id)
        print(f"Набор {args.dataset_id}: {info.get('Caption')}")
        print(f"  геоданные: {info.get('ContainsGeodata')}, "
              f"обновлён: {info.get('VersionDate')}")
        print(f"  записей: {dataset_count(args.dataset_id)}")
        columns = info.get("Columns") or []
        print(f"  колонок: {len(column_names(info))}")
        for column in columns:
            print(f"    {column.get('Name')} — {column.get('Caption')} "
                  f"({column.get('Type')})")
            for sub in column.get("SubColumns") or []:
                print(f"      {sub.get('Name')} — {sub.get('Caption')} "
                      f"({sub.get('Type')})")
    except MosRuError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if args.features:
        payload = features(args.dataset_id, top=1)
        items = payload.get("features") or []
        print(f"  пример geometry: {items[0].get('geometry') if items else 'нет'}")
        if items:
            print(f"  ключи attributes: {sorted((items[0].get('properties') or {}).get('Attributes', {}))}")
    elif args.rows:
        for item in rows(args.dataset_id, top=args.rows):
            cells = item.get("Cells") or {}
            print(f"  global_id={item.get('global_id')}: "
                  f"{ {k: cells[k] for k in list(cells)[:6]} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
