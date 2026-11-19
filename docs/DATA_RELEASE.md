# Data-снимки и онлайн-деплой

Данные (`web/public/data`) в `main` не хранятся: 20 МБ geojson + маршруты.
Они лежат отдельным снимком в ветке **`data`** как `data.zip`. Код и данные
версионируются раздельно; сайт всегда знает, на какой снимок смотрит.

## 1. Собрать архив (на машине, где есть актуальные данные)

PowerShell из корня репозитория:

```powershell
Compress-Archive -Path web/public/data/* -DestinationPath data.zip -Force
```

Внутри `data.zip`:

```text
presnya_buildings.geojson
presnya_poi.geojson
presnya_redzones.geojson
basemap.geojson        # подложка: улицы, вода, парки (pipeline/build_basemap.py)
routes/
isochrones/
```

`basemap.geojson` собирается из локального OSM-дампа:

```powershell
pipeline\.venv\Scripts\python.exe pipeline\build_basemap.py `
  --pbf <ASCII-путь>\moscow.osm.pbf `
  --out web\public\data\basemap.geojson
```

Файл ~1,3 МБ (gzip на Pages ≈ 130 КБ). Река — осевая линия OSM
(отношение 389341), геометрия отношения кэшируется в
`data/raw/moskva-river-389341.json`.

`data.zip` в `.gitignore` — в `main` не коммитим.

## 2. Обновить ветку `data`

Ветка `data` — отдельная, без кода. Первый раз она уже создана. Обновление:

```powershell
git fetch origin data
git worktree add --detach C:\Users\starg\karta-data-branch
cd C:\Users\starg\karta-data-branch
git switch data
git rm -rf .
Copy-Item -Force <путь-к-репозиторию>\data.zip .\data.zip
git add data.zip
git commit -m "data snapshot YYYY-MM-DD"
git push origin data
cd <путь-к-репозиторию>
git worktree remove --force C:\Users\starg\karta-data-branch
```

Если ветки `data` ещё нет:

```powershell
git checkout --orphan data
git rm -rf .
```

## 3. Запустить деплой

Обычно деплой запускается сам после merge в `main` (autopilot → Deploy).
Ручной запуск нужен для отката данных: GitHub → Actions → **Deploy** →
Run workflow, `data_ref: data`.

## 4. Защита

- Deploy запускается по push в `main` и вручную; перед сборкой данные
  проверяет `pipeline/validate_export.py`.
- Бот (`autopilot.yml`) мержит PR только после зелёных тестов.
- Ветка `gh-pages` защищена от force push и удаления; агенту push туда запрещён.
- `GITHUB_TOKEN` живёт только внутри Actions; на ноутбук не попадает.

## 5. Что дальше

- `data.yml` будет собирать `data.zip` автоматически (PostGIS + OSM +
  data.mos.ru) и обновлять ветку `data`.
- Valhalla — отдельной фазой: тяжёлые тайлы, кэш, только изменённые дома.
