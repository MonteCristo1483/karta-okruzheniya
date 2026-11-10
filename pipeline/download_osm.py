#!/usr/bin/env python3
"""Скачивает выгрузку OSM по Москве с download.geofabrik.de в data/raw/moscow.osm.pbf.

При обрыве связи докачивает файл с места обрыва (HTTP Range), повторные запуски
продолжают уже скачанное. Итоговый размер сверяется с размером, объявленным
сервером. Сам .pbf в git не коммитится (см. .gitignore).
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

URL = "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
TARGET = Path(__file__).resolve().parent.parent / "data" / "raw" / "moscow.osm.pbf"
CHUNK_SIZE = 1 << 20  # 1 МиБ
ATTEMPTS = 5
RETRY_DELAY_S = 5


def parse_total_size(headers: Message, status: int) -> int | None:
    if status == 206:
        content_range = headers.get("Content-Range", "")  # "bytes 0-0/12345"
        if "/" in content_range:
            tail = content_range.split("/")[-1]
            return int(tail) if tail.isdigit() else None
        return None
    length = headers.get("Content-Length")
    return int(length) if length and length.isdigit() else None


def probe_total_size() -> int | None:
    request = urllib.request.Request(URL)
    request.add_header("Range", "bytes=0-0")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return parse_total_size(response.headers, response.status)
    except (urllib.error.URLError, OSError):
        return None


def report(done: int, total: int | None, transferred: int, elapsed: float) -> None:
    speed = transferred / (1 << 20) / max(elapsed, 1e-6)
    if total:
        print(
            f"\r{done / (1 << 20):.0f} / {total / (1 << 20):.0f} МиБ"
            f" ({done / total * 100:.1f}%) — {speed:.1f} МиБ/с",
            end="",
            flush=True,
        )
    else:
        print(f"\r{done / (1 << 20):.1f} МиБ — {speed:.1f} МиБ/с", end="", flush=True)


def transfer() -> tuple[bool, int | None]:
    """Одна попытка скачивания. Возвращает (завершено, размер_на_сервере)."""
    have = TARGET.stat().st_size if TARGET.exists() else 0
    request = urllib.request.Request(URL)
    if have:
        request.add_header("Range", f"bytes={have}-")

    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status == 206:
            mode = "ab"
            done = have
        elif response.status == 200:
            if have:
                print("\nСервер не дал докачку (200 вместо 206), скачиваю заново")
            mode = "wb"
            done = 0
        else:
            raise RuntimeError(f"неожиданный статус ответа: {response.status}")
        total = parse_total_size(response.headers, response.status)

        transferred = 0
        started = time.monotonic()
        next_report = started + 2
        with TARGET.open(mode) as out:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                transferred += len(chunk)
                now = time.monotonic()
                if now >= next_report:
                    report(done, total, transferred, now - started)
                    next_report = now + 2
    print()
    return total is None or done >= total, total


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    total: int | None = None

    for attempt in range(1, ATTEMPTS + 1):
        try:
            completed, total = transfer()
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                total = probe_total_size()
                if total is not None and TARGET.exists() and TARGET.stat().st_size == total:
                    completed = True
                else:
                    print("Ошибка HTTP 416: локальный файл не совпадает с сервером", file=sys.stderr)
                    return 1
            else:
                print(f"HTTP-ошибка {exc.code}: {exc.reason}", file=sys.stderr)
                completed = False
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            print(f"\nСоединение прервано: {exc}. Скачанное сохранено, повторный запуск докачает.", file=sys.stderr)
            completed = False

        if completed:
            break
        if attempt < ATTEMPTS:
            print(f"Попытка {attempt + 1}/{ATTEMPTS} через {RETRY_DELAY_S} с...")
            time.sleep(RETRY_DELAY_S)
    else:
        print("Не удалось скачать файл за все попытки.", file=sys.stderr)
        return 1

    final_size = TARGET.stat().st_size
    if total is not None and final_size != total:
        print(f"Ошибка размера: скачано {final_size} байт, сервер объявлял {total}.", file=sys.stderr)
        return 1
    print(f"Готово: {TARGET} ({final_size / (1 << 20):.1f} МиБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
