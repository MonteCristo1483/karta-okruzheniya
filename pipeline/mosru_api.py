"""Клиент API портала открытых данных Москвы (apidata.mos.ru).

Ключ у портала один на аккаунт (в настройках профиля), передаётся как
`?api_key=...`. В репозиторий и в чат ключ не попадает: читаем
`MOSRU_API_KEY` из окружения или из `.env` в корне (файл в .gitignore).

Использование:
  from mosru_api import datasets, dataset_info, dataset_count, rows, features
  dataset_info(62329)  # структура набора: колонки, описание
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://apidata.mos.ru/v1"
PAGE = 1000  # ограничение портала на одну страницу


class MosRuError(RuntimeError):
    """Ошибка API или отсутствие ключа — с понятным текстом для человека."""


def load_api_key(env_path: Path | None = None) -> str | None:
    key = os.environ.get("MOSRU_API_KEY")
    if key and key.strip():
        return key.strip()
    path = env_path or (ROOT / ".env")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "MOSRU_API_KEY":
                value = value.strip().strip('"').strip("'")
                return value or None
    return None


def require_key(key: str | None = None) -> str:
    value = key or load_api_key()
    if not value:
        raise MosRuError(
            "нет ключа MOSRU_API_KEY: скопируйте .env.example в .env и вставьте ключ "
            "из профиля на apidata.mos.ru (файл не коммитится)"
        )
    return value


def http_json(url: str, timeout: float = 30.0):
    request = urllib.request.Request(
        url, headers={"User-Agent": "karta-okruzheniya/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = " (проверьте ключ)" if exc.code == 401 else ""
        raise MosRuError(
            f"HTTP {exc.code}{hint}: {url.split('?', 1)[0]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise MosRuError(f"сеть недоступна: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise MosRuError(f"ответ не JSON: {exc}") from exc


def _url(path: str, key: str, params: dict | None = None) -> str:
    query = {k: v for k, v in (params or {}).items() if v is not None}
    query["api_key"] = key
    return f"{BASE}{path}?{urllib.parse.urlencode(query)}"


def datasets(key: str | None = None, top: int = PAGE, skip: int = 0,
             fetch=http_json) -> list[dict]:
    payload = fetch(_url("/datasets", require_key(key), {"$top": top, "$skip": skip}))
    # Портал отвечает объектом {"Items": [...], "Count": N}, но при
    # $inlinecount может отдать и просто массив — поддерживаем оба вида.
    if isinstance(payload, dict):
        return payload.get("Items") or payload.get("items") or []
    return payload


def dataset_info(dataset_id: int, key: str | None = None, fetch=http_json) -> dict:
    payload = fetch(_url(f"/datasets/{dataset_id}", require_key(key)))
    if isinstance(payload, list):
        if not payload:
            raise MosRuError(f"набор {dataset_id} не найден")
        return payload[0]
    return payload


def dataset_count(dataset_id: int, key: str | None = None, fetch=http_json) -> int:
    payload = fetch(_url(f"/datasets/{dataset_id}/count", require_key(key)))
    try:
        return int(payload)
    except (TypeError, ValueError) as exc:
        raise MosRuError(f"неожиданный ответ count: {payload!r}") from exc


def rows(dataset_id: int, key: str | None = None, top: int = PAGE, skip: int = 0,
         fetch=http_json) -> list[dict]:
    return fetch(
        _url(f"/datasets/{dataset_id}/rows", require_key(key),
             {"$top": top, "$skip": skip})
    )


def features(dataset_id: int, key: str | None = None, top: int = PAGE, skip: int = 0,
             bbox: tuple[float, float, float, float] | None = None,
             fetch=http_json) -> dict:
    params: dict = {"$top": top, "$skip": skip}
    if bbox is not None:
        params["bbox"] = ",".join(str(value) for value in bbox)
    return fetch(_url(f"/datasets/{dataset_id}/features", require_key(key), params))


def column_names(info: dict) -> list[str]:
    """Плоский список имён колонок (включая вложенные SubColumns)."""
    names: list[str] = []

    def walk(columns):
        for column in columns or []:
            names.append(column.get("Name", ""))
            walk(column.get("SubColumns"))

    walk(info.get("Columns"))
    return [name for name in names if name]
