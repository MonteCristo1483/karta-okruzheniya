"""Строка подключения к PostGIS.

Секрет живёт в окружении или в `.env` в корне проекта (файл в .gitignore).
В коде остаётся только дефолт локального docker-compose (`okrug_local_dev`),
чтобы `docker compose up -d` работал без дополнительной настройки.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

DEFAULTS = {
    "PGHOST": "localhost",
    "PGPORT": "5432",
    "PGDATABASE": "okrug",
    "PGUSER": "okrug",
}
DEFAULT_PASSWORD = "okrug_local_dev"


def _read_dotenv(path: Path) -> dict[str, str]:
    """Простые пары KEY=VALUE из `.env`; кавычки и комментарии — как в mosru_api."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_dotenv(ENV_PATH)


def _get(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    value = _DOTENV.get(name)
    return value if value else None


def _build_dsn() -> str:
    explicit = _get("OKRUG_DSN")
    if explicit:
        return explicit
    host = _get("PGHOST") or DEFAULTS["PGHOST"]
    port = _get("PGPORT") or DEFAULTS["PGPORT"]
    dbname = _get("PGDATABASE") or DEFAULTS["PGDATABASE"]
    user = _get("PGUSER") or DEFAULTS["PGUSER"]
    password = _get("PGPASSWORD") or _get("OKRUG_PASSWORD") or DEFAULT_PASSWORD
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


DB_DSN = _build_dsn()
