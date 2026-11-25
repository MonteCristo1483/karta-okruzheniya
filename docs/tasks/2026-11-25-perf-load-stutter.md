# Задача PERF-LOAD: убрать просадки FPS при подгрузках тайлов и перелёте к дому

Дата: 2026-11-25. **Модель: DeepSeek V4.1 Flash (`opencode-go/deepseek-v4.1-flash`).**
Полоса: только `web/app/map.tsx`. Данные, пайплайн, стили слоёв, скоринг и карточку не трогать.

## Контекст и диагноз

После BM-LOW (#201) обзор `z10–11` на устройстве владельца даёт 30–50 FPS, но остались **просадки при подгрузках** (панорама на `z12.6+` и перелёт `flyTo` к выбранному дому). Причины в `web/app/map.tsx`:

1. **`refreshBasemapDecorations` (строки ~3625–3690) стреляет на каждый тайл во время движения:**
   - Подписан на `map.on("sourcedata")` (строка 3681): при подгрузке тайлов подложки на `z ≥ 12.6` вызывается на **каждый тайл прямо во время полёта/скролла**.
   - Опрашивает через `querySourceFeatures("basemap")` 6 слоёв (`landuse`, `green`, `water`, `river`, `waterway`, `road`), хотя `landuse` и `waterway` **нигде не используются** ни в `buildLabelCandidates` (строка 2044), ни в `buildTreePoints` (строка 1976).
   - Для **каждой** фичи (включая тысячи безымянных сегментов дорог) вызывает `JSON.stringify(feature.geometry)` для дедупликации, а затем в `buildLabelCandidates` отбрасывает все фичи без `name`.
2. **`sourcedata` зданий и `setData` красных зон во время `flyTo`:**
   - Строки ~4723–4728: `map.on("sourcedata")` для `buildings` вызывает `scheduleSelectedCapture(selectedId, captureToken)` на каждый пришедший тайл зданий прямо во время `flyTo`, хотя `flyToBuilding` (строка 4830) уже ставит `selectionMovePending = true` и ждёт `moveend`.
   - Строки ~4957–4961: в `selectById` `ensureRedzonesForDistrict(district.id)` сразу по окончании fetch вызывает `applyRedzoneSource()` → `source.setData(...)` и `rebuildOverlays(...)` прямо посреди анимации `flyTo`.

## Что сделать (точечно в `web/app/map.tsx`)

1. **Облегчить и успокоить `refreshBasemapDecorations` (строки ~3625–3690):**
   - Не выполнять сбор фич, пока `map.isMoving()` (по окончании движения его всё равно вызовет `moveend`).
   - В обработчике `sourcedata` для `basemap` вызывать обновление только если `!map.isMoving()`, и заменить мгновенный `requestAnimationFrame` на короткий debounce (`~120 мс`), чтобы пачка догрузившихся тайлов обрабатывалась один раз.
   - Убрать `"landuse"` и `"waterway"` из списка опрашиваемых `sourceLayer` (оставить `["green", "water", "river", "road"]`).
   - Фильтровать фичи **до** дедупликации и без `JSON.stringify(feature.geometry)`:
     - для `"road"`, `"river"`, `"water"` сразу пропускать фичи без имени (`if (!properties?.name) continue`);
     - для `"green"` пропускать фичи, если нет `properties?.name` **и** (`zoom < TREE_MIN_ZOOM` или `(properties?.class !== "park" && properties?.class !== "wood")`);
     - вместо `JSON.stringify(feature.geometry)` использовать лёгкий ключ дедупликации: `feature.id` (если есть) + первая координата геометрии (округлённая до `1e-5`).
2. **Не рвать анимацию `flyTo` выбором дома (строки ~4723–4728 и ~4957–4961):**
   - В `map.on("sourcedata")` для `buildings` (строка ~4725): не вызывать `scheduleSelectedCapture`, пока `selectionMovePending || map.isMoving()`.
   - В `ensureRedzonesForDistrict` / `selectById` (строки ~3027–3037, ~4959): сетевой `loadDistrictRedzones(districtId)` запускать сразу, но если `map.isMoving()`, откладывать `applyRedzoneSource()` и `rebuildOverlays` до `moveend` (`map.once("moveend", ...)`).

## Проверка

- `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json` и `npm run build` в `web/`.
- Прогнать существующие e2e (`npx playwright test`).
- Локальный замер с `?perf=1`:
  - панорама/подгрузка на `z13.5` и `z16.6` (FPS / p95 / max frame ms);
  - перелёт между двумя жилыми домами из разных районов (FPS / p95 во время перелёта).
- Визуально на `z13` и `z16.6` (после остановки камеры): подписи улиц/парков/воды, деревья на `z15+`, красные зоны, маршрут и 3D-окна выбранного дома на месте.

## Отчёт (≤25 строк)

1. `git diff --stat`.
2. Замеры FPS / p95 на `z13.5`, `z16.6` и при перелёте между домами.
3. Статус `tsc`, `build`, `e2e`.
