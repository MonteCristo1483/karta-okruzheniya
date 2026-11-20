# Задача C: тайлы в CI, service worker и smoke-тест 206

Дата: 2026-11-20. Финальный шаг PMTiles: тайлы впервые поедут на прод.
Клиент уже умеет оба режима, поэтому откат — просто отсутствие файла.

## Прочитать
1. `docs/research/pmtiles-plan.md` — «Факт замера» и «Риски»
2. `docs/STATE.md`
3. `.github/workflows/deploy.yml` (весь, он короткий)
4. `web/public/sw.js` — обработчик `fetch` и `networkFirst`
5. `pipeline/build_tiles.py` — только `main()`/аргументы и как вызывается Docker

Не читай `*.geojson`, `dist/`, `node_modules/`.

## Задача
1. **Сборка тайлов в Deploy** (`.github/workflows/deploy.yml`), после
   `Validate data` и `Check golden facts`, до `npm run build`:
   - собрать образ: `docker build -f pipeline/docker/tippecanoe.Dockerfile
     -t karta-tippecanoe:2.79.0 .` (на ubuntu-раннере Docker есть);
   - `python3 pipeline/build_tiles.py`;
   - переименовать `web/public/data/tiles/buildings-<sha8>.pmtiles` в
     `buildings.pmtiles` (клиент ждёт именно это имя) и удалить хешированный
     дубль, чтобы не публиковать лишние 761 КБ.
2. **Service worker** (`web/public/sw.js`): не перехватывать `*.pmtiles` —
   ранний `return` в обработчике `fetch` (ответ `206` нельзя класть в Cache
   API, а Range-запросы должны идти напрямую). Остальные `data/*` — как есть.
3. **Smoke-тест 206** после публикации: `curl -r 0-99` по
   `https://montecristo1483.github.io/karta-okruzheniya/data/tiles/buildings.pmtiles`,
   ждём `206`, 3 попытки с паузой, `continue-on-error: true` (предупреждение,
   а не падение деплоя).
4. **geojson из `dist` пока НЕ удалять** — он остаётся фолбэком клиента.
   Уборка — отдельным шагом, когда тайлы отработают на проде.

## Что нельзя
- Менять `scoring.py`, `config/scoring.json`, данные, ветку `data`.
- Трогать клиентскую логику в `web/app/*` (она уже готова).
- Коммитить `*.pmtiles`/`buildings-index.json` (артефакты, gitignored).
- main/gh-pages, force-push, `npm run deploy`.

## Ветка
```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/pmtiles-c origin/main
```

## Проверка
- Deploy зелёный, укладывается в 10 минут (Docker-сборка ~2 мин).
- На проде: `HEAD data/tiles/buildings.pmtiles` → 200; сайт перешёл в
  тайловый режим (в консоли `addProtocol`, в сети — Range-запросы).
- 5 контрольных домов: 6.3 / 5.9 / 0.0 / 9.3 / 8.4, карточка с разбивкой
  и строкой «хуже N%».
- Поиск, `?building=`, подсветка, маршруты, изохроны — работают.
- `?tiles=0` — сайт на geojson (фолбэк жив).
- Первый экран зданий < 300 КБ (DevTools).
- В Cache API нет `*.pmtiles`.

## Критерий
Прод на тайлах, Deploy < 10 мин, фолбэк работает, ошибок в консоли нет;
данные и формула не менялись. Не более 40 минут.
