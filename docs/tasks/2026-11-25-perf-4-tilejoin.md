# Подзадача PERF-4-TILEJOIN: единый тайловый запрос (`tile-join`) + полные классы магистралей

Дата: 2026-11-25. **Модель: DeepSeek V4.1 Flash (`opencode-go/deepseek-v4.1-flash#high`).**
Полоса: `pipeline/import_linear.py`, `pipeline/build_overlays.py`, `web/app/map.tsx`.

## Диагноз

1. Сейчас на каждый квадрат карты браузер шлёт отдельные HTTP Range-запросы в `basemap.pmtiles` и `overlays.pmtiles`: из-за очереди соединений тайл зданий/подложки может отрисоваться на 200–400 мс раньше тайла красных зон.
2. В `pipeline/import_linear.py` в `ROAD_CLASSES` входят только `("trunk", "primary", "secondary")` — пропущены `motorway`, съезды развязок (`motorway_link`, `trunk_link`, `primary_link`, `secondary_link`) и (по решению владельца) `tertiary`.

## Что сделать

1. В `pipeline/build_overlays.py` (для слоя отображения `redzones`, **не меняя `config/scoring.json` и скоринг**):
   - включить все магистрали, развязки и очень активные автомобильные дороги Москвы (из PBF / PostGIS):
     - `z8+`: `motorway`, `motorway_link`, `trunk`, `trunk_link`, `primary`, `primary_link` и `railway` (`rail`, `narrow_gauge`, `light_rail`);
     - `z12+`: `secondary`, `secondary_link` + активные многополосные `tertiary` (`lanes >= 3` или односторонние набережные/проезды с `lanes >= 2`);
     - `z13+`: полигоны `industrial` и `construction`;
   - на этапе сборки `build_overlays.py` склеивать базовую подложку и оверлеи через `tile-join` (в контейнере `karta-tippecanoe:2.79.0`) либо отдавать слои из единого PMTiles-источника, чтобы подложка дороги и её красное свечение всегда приходили в одном тайле `z/x/y`.
2. Обновить источник в `web/app/map.tsx` и проверить `validate_export.py`, unit-тесты и e2e.

## Отчёт (≤15 строк)

1. `git diff --stat`.
2. Число фич `redzones`, размер PMTiles и замер FPS.
3. Статус тестов.
