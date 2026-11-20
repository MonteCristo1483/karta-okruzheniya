# Задача D2: пароль PostGIS из окружения

Дата: 2026-11-20. Гигиена/секреты. Поведение локальной разработки
не меняется: дефолт остаётся `okrug_local_dev`, как в `docker-compose.yml`.

## Прочитать
1. `docs/DEBT.md` — пункт 3
2. `docs/SECURITY.md` — раздел про пароль
3. `docker-compose.yml` (строки 5–15)
4. `pipeline/export_map_data.py` (первые 30 строк) — как объявлен `DB_DSN`

Не читай `*.geojson`, `data/`, `web/`.

## Задача
1. Новый `pipeline/db.py`:
   - `DB_DSN` собирается из окружения: `OKRUG_DSN`, иначе из
     `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (или
     `OKRUG_PASSWORD`), с теми же дефолтами, что в `docker-compose.yml`
     (`localhost`, `5432`, `okrug`, `okrug`, `okrug_local_dev`);
   - короткий docstring: секрет живёт в `.env`, в коде только дефолт для
     локального docker-compose.
2. Во всех местах, где сейчас литерал
   `"host=localhost port=5432 dbname=okrug user=okrug password=okrug_local_dev"`,
   заменить на `from db import DB_DSN` (плоский импорт — так уже делают
   тесты: `from check_golden import check`).
   Файлы (15): build_golden, check_construction, check_layers, check_pareto,
   check_parks, export_map_data, extract_districts, extract_presnensky,
   import_buildings, import_build_year, import_linear, import_mosru_metro,
   import_mosru_parks, scoring, routes_valhalla (проверить полный список
   `grep -rn "okrug_local_dev" pipeline/`).
3. В `.env.example` добавить `OKRUG_PASSWORD=` (закомментированный пример),
   в `docs/SECURITY.md` — строку, что пароль читается из окружения.
4. Не менять сам пароль в `docker-compose.yml`.

## Проверка
- `grep -rn "okrug_local_dev" pipeline/` — остаётся только `pipeline/db.py`
  (и, если нужно, тестовые фикстуры).
- `pipeline\.venv\Scripts\python.exe -m unittest discover -s pipeline -p "test_*.py"` — 60 OK.
- `pipeline\.venv\Scripts\python.exe pipeline\export_map_data.py --help`
  (или `python -c "import db; print(db.DB_DSN)"` из папки pipeline) —
  импорт работает, DSN собирается.
- Запуск с `OKRUG_PASSWORD=test` даёт `password=test` в DSN.

## Что нельзя
- Менять формулу/числа (`config/scoring.json`, `scoring.py` — только DSN).
- Трогать данные, ветки `data`/`gh-pages`, workflows.

## Ветка
```powershell
cd C:\Users\starg\karta-pipeline
git fetch origin
git switch -c task/hygiene-secrets origin/main
```

## Отчёт
1. Список файлов и что изменено. 2. Проверка grep. 3. Тесты 60/60.
4. Как задать пароль через окружение. 5. PR.
Не более 25 минут.
