# Задача B2: клиент на PMTiles (с откатом на geojson)

Дата: 2026-11-20. Главный шаг миграции. **Прод не должен сломаться:**
тайлов на проде ещё нет, клиент обязан молча остаться на geojson.

## Прочитать
1. `docs/research/pmtiles-plan.md` — раздел «Факт замера»
2. `docs/STATE.md`
3. `web/app/map.tsx` — источники `buildings`, слои, `selectById`, поиск,
   квинтили цвета, `?building=`, загрузка `routes/<id>.json`
4. `web/app/building-sheet.tsx` — откуда берётся `breakdown`

Не читай `*.geojson`, `dist/`, `node_modules/`, `docs/research/*` кроме плана.

## Контекст
Тайлы уже собираются (`pipeline/build_tiles.py`, Docker-образ
`karta-tippecanoe:2.79.0` на машине), `breakdown` уже лежит в
`routes/<id>.json` на проде. В тайлах остаются `id, index, height, address,
year`. Клиент сейчас держит в памяти весь geojson: из него поиск, центры
домов, квинтили цвета, перцентили, `?building=` и карточка.

## Задача
1. Зависимость: `pmtiles` **4.5.0** в `package.json`, точно без `^`/`~`.
2. **Проба тайлов**: `fetch("data/tiles/buildings.pmtiles", { method: "HEAD" })`.
   - `200` → режим тайлов;
   - иначе → **текущий путь на geojson без изменений** (на проде сейчас
     именно так, поэтому деплой ничего не ломает).
   Отладочный форс: `?tiles=0` — всегда geojson, `?tiles=1` — всегда тайлы.
3. Регистрация протокола: `maplibregl.addProtocol("pmtiles", new Protocol().tile)`
   внутри `useEffect` **один раз** (флаг на модуле), не на уровне модуля —
   static export пререндерится в Node.
4. Источник зданий: `{ type: "vector", url: "pmtiles://<абсолютный URL>" }`,
   во все слои зданий добавить `"source-layer": "buildings"`. Проверить
   все слои с `source: "buildings"`: экструзия, roof-cap, обводки,
   подсветка выбора.
5. `setFeatureState`/`queryRenderedFeatures` по тайлам: id фичи даёт
   tippecanoe (`--use-attribute-for-id=id`), поэтому выбор дома и подсветка
   должны работать без `promoteId`. Если id приходит строкой — привести.
6. **Индекс** `data/buildings-index.json` (`[id, lon, lat, address, index,
   year]`) — источник для: поиска по адресу, центров домов (flyTo),
   квинтилей цвета, перцентилей, deep link `?building=`.
7. **Карточка**: `breakdown` брать из `routes/<id>.json` (уже есть на проде).
   На geojson-пути оставить как сейчас.
8. Ничего не удалять: geojson-путь остаётся рабочим и отлаживаемым.

## Локальная проверка
```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/pmtiles-b2 origin/main
python pipeline\add_breakdown_to_routes.py --data-dir web\public\data   # локальные данные догнать до прода
docker run --rm -v "${PWD}:/work" -w /work karta-tippecanoe:2.79.0 tippecanoe --version
# собрать тайлы: pipeline\build_tiles.py (см. его --help), затем
# переименовать buildings-<sha8>.pmtiles в web\public\data\tiles\buildings.pmtiles
```
Сборка и проверка — на ASCII-пути, как обычно: `tsc`, `npm run build`,
локальный просмотр в WebKit.

## Что нельзя
- Менять `config/scoring.json`, `pipeline/scoring.py`, данные, `sw.js`,
  `.github/workflows/*`, генератор `/r/`.
- Удалять `presnya_buildings.geojson` из `public/data` (это шаг C).
- main/gh-pages, force-push, `npm run deploy`.

## Проверка (WebKit)
- 5 контрольных домов дают те же цифры, что сейчас: 64091236 — 6.3,
  673526644 — 5.9, 2472693506 — 0.0, 95364434 — 9.3, 16713139 — 8.4.
- Поиск по адресу, `?building=<id>`, подсветка, зум после выбора, слои
  POI/зон/маршрутов/изохрон — работают.
- Скриншоты `?tiles=1` и `?tiles=0`: цвет зданий и общий вид визуально
  совпадают (1290 light/dark, 390 light).
- Консоль чистая; в сети первый экран зданий < 300 КБ.

## Критерий
- `?tiles=0` — сайт работает как до задачи (регресса нет).
- `?tiles=1` — те же 5 домов, карточка с разбивкой, поиск, deep link.
- `tsc`/`build` зелёные; прод-поведение без тайлов не изменилось.
Не более 70 минут. Если упираешься в несовместимость — стоп и отчёт
с конкретным местом, не переписывай карту целиком.
