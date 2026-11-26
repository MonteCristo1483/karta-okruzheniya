#!/usr/bin/env python3
"""Сборка розы ветров Москвы по ERA5 (Open-Meteo Archive API).

Бесплатно, без ключа. Почасовой wind_direction_10m / wind_speed_10m за
2015-01-01 .. 2025-12-31, точка Москва (55.7558, 37.6173). Направление —
метеорологическое (откуда дует). 16 секторов по 22.5°, сектор центрирован
на румбе. Штиль (< 0.5 м/с) исключается: у него нет направления.

Результат — config/wind_rose.json (доли румбов, сумма 1.0). Повторный
запуск перезаписывает файл (идемпотентно по периоду и точке).

  python pipeline/build_wind_rose.py
  python pipeline/build_wind_rose.py --start 2015 --end 2025
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "wind_rose.json"
URL = "https://archive-api.open-meteo.com/v1/archive"
LAT, LON = 55.7558, 37.6173
SECTOR_LABELS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)
CALM_KMH = 1.8  # 0.5 м/с


def fetch_year(year: int) -> dict:
    query = (
        f"?latitude={LAT}&longitude={LON}"
        f"&start_date={year}-01-01&end_date={year}-12-31"
        "&hourly=wind_direction_10m,wind_speed_10m&timezone=Europe%2FMoscow"
    )
    with urllib.request.urlopen(URL + query, timeout=120) as response:
        return json.load(response)


def sector_index(direction: float) -> int:
    return int(math.floor((((direction % 360.0) + 11.25) % 360.0) // 22.5))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Роза ветров Москвы (ERA5)")
    parser.add_argument("--start", type=int, default=2015)
    parser.add_argument("--end", type=int, default=2025)
    args = parser.parse_args(argv)

    counts = [0] * 16
    calm = 0
    hours = 0
    grid = None
    for year in range(args.start, args.end + 1):
        payload = fetch_year(year)
        if grid is None:
            grid = {
                "lat": payload.get("latitude"),
                "lon": payload.get("longitude"),
                "elevation_m": payload.get("elevation"),
            }
        directions = payload["hourly"]["wind_direction_10m"]
        speeds = payload["hourly"]["wind_speed_10m"]
        for direction, speed in zip(directions, speeds):
            if direction is None:
                continue
            if speed is not None and speed < CALM_KMH:
                calm += 1
                continue
            counts[sector_index(float(direction))] += 1
            hours += 1

    shares = {label: round(counts[i] / hours, 6) for i, label in enumerate(SECTOR_LABELS)}
    # Ровно 1.0 после округления: остаток отдаём самому частому румбу.
    top = max(shares, key=shares.get)
    shares[top] = round(shares[top] + (1.0 - sum(shares.values())), 6)

    payload = {
        "meta": {
            "title": "Роза ветров Москвы, 16 румбов",
            "source": (
                "ERA5 (ECMWF Reanalysis v5) via Open-Meteo Archive API, "
                "hourly wind_direction_10m / wind_speed_10m"
            ),
            "source_url": URL,
            "point": {"lat": LAT, "lon": LON, "name": "Москва (центр)"},
            "grid_point": grid,
            "period": f"{args.start}-01-01 .. {args.end}-12-31",
            "fetched_at": date.today().isoformat(),
            "timezone": "Europe/Moscow",
            "method": (
                "Доля часов по 16 секторам по 22.5°, сектор центрирован на "
                "румбе (N = 348.75..11.25°). Направление — метеорологическое "
                "(откуда дует). Часы со скоростью < 0.5 м/с (штиль) исключены."
            ),
            "hours_used": hours,
            "hours_calm": calm,
            "license": (
                "ERA5: Copernicus Climate Change Service (C3S), CC BY 4.0; "
                "Open-Meteo: CC BY 4.0"
            ),
        },
        "order": list(SECTOR_LABELS),
        "shares": shares,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"Записано: {OUT} (часов {hours:,}, штиль {calm:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
