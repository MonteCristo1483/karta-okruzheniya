# Задача TILES-OVERLAYS: единый городской `data/overlays.pmtiles` (redzones + poi + trees)

Дата: 2026-11-25. **Модель: DeepSeek V4.1 Flash (`opencode-go/deepseek-v4.1-flash`).**
Полоса: `pipeline/` (`pipeline/build_overlays.py`, `pipeline/run_city.py`, `pipeline/validate_export.py`, `pipeline/test_validate_export.py`). Файлы `web/` не трогать.

## Контекст

Сейчас `buildings.pmtiles` и `basemap.pmtiles` переведены в PMTiles, но красные зоны (`redzones`), точки интереса (`poi`) и деревья (`trees`) остались на 132 по-районных `.geojson` и JS-генерации в браузере. Из-за этого Москва грузится кусками по границам районов, а на обзоре `z9–11` красные магистрали выключены.

Нужно собрать единый модульный векторный тайловый файл **`data/overlays.pmtiles` (`z8–16`)** с тремя слоями (`redzones`, `poi`, `trees`) без разрезов по границам районов.

## Что сделать

1. Создать **`pipeline/build_overlays.py`**:
   - **`redzones` (из PostGIS, в пределах `moscow` с `margin = 0.02°`, без дублей и без нарезки по районам):**
     - Линейные объекты из таблицы `linear`:
       - `kind = 'road' AND class IN ('trunk', 'primary')` и `kind = 'railway' AND class IN ('rail', 'narrow_gauge', 'light_rail')` → `"tippecanoe": {"layer": "redzones", "minzoom": 8}`, `properties: {"kind": kind, "class": cls}`.
       - `kind = 'road' AND class = 'secondary'` → `"tippecanoe": {"layer": "redzones", "minzoom": 12}`, `properties: {"kind": kind, "class": cls}`.
     - Полигональные зоны из таблицы `zones` (`ST_Intersects(z.geometry, moscow)`):
       - `kind IN ('industrial', 'construction')` → `"tippecanoe": {"layer": "redzones", "minzoom": 13}`, `properties: {"kind": kind}`.
   - **`poi` (из PostGIS `poi` в пределах `moscow`, с фильтрацией `shop_subtypes` из `config/scoring.json` как в `export_map_data.py`):**
     - `type IN ('subway_entrance', 'rail_platform')` → `"tippecanoe": {"layer": "poi", "minzoom": 11}`, `properties: {"type": type, "subtype": subtype, "name": name}`.
     - `type IN ('school', 'kindergarten', 'park', 'shop')` → `"tippecanoe": {"layer": "poi", "minzoom": 13}`, `properties: {"type": type, "subtype": subtype, "name": name}`.
   - **`trees` (детерминированные точки крон внутри полигонов `green` с `class IN ('park', 'wood')` Москвы):**
     - Взять полигоны `green` (`park`/`wood`) Москвы (из `basemap` / PostGIS или `data/city/.basemap.ndjson`, либо сгенерировать тем же детерминированным PRNG, что в `buildTreePoints`: 60–78 точек/га, лимит 220 точек на полигон, `area >= 0.04 га`, `properties: {"size": round(0.78..1.40, 2), "tone": 0|1}`).
     - Записать точки со свойством `"tippecanoe": {"layer": "trees", "minzoom": 15}`.
   - Собрать `data/overlays.pmtiles` через Docker `karta-tippecanoe:2.79.0`:
     - флаги: `--force -o /work/data/overlays.pmtiles -Z8 -z16 -d13 -r1 --drop-densest-as-needed -al -ap --maximum-tile-bytes 220000 --no-tile-stats`.
   - Скопировать `data/overlays.pmtiles` в `web/public/data/overlays.pmtiles` (если папка существует).
2. Подключить сборку `overlays.pmtiles` в **`pipeline/run_city.py`** (в `stage_tiles(force)` рядом с `buildings.pmtiles` и `basemap.pmtiles`).
3. В **`pipeline/validate_export.py`** (формат `new`) добавить проверку наличия непустого `overlays.pmtiles` (или warning, если файл отсутствует в тестовых фикстурах, чтобы не ломать существующие unit-тесты; добавить unit-тест в `pipeline/test_validate_export.py`).

## Проверка

- Запустить `pipeline/.venv/Scripts/python.exe pipeline/build_overlays.py` и убедиться, что `data/overlays.pmtiles` создан, содержит слои `redzones`, `poi`, `trees`, размер файла разумен (~5–20 МБ).
- Прогнать `pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v` и `pipeline/.venv/Scripts/python.exe pipeline/validate_export.py data --format new`.

## Отчёт (≤25 строк)

1. `git diff --stat`.
2. Число фич по слоям (`redzones`, `poi`, `trees`), размер `data/overlays.pmtiles` и число тайлов.
3. Результаты unit-тестов и `validate_export.py`.
