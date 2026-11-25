# Задача WEB-NEON-OVERLAYS: бесшовные тайлы `overlays.pmtiles` + возврат Neon на всю Москву

Дата: 2026-11-25. **Модель: DeepSeek V4.1 Flash (`opencode-go/deepseek-v4.1-flash`).**
Полоса: только `web/app/map.tsx` (и `.github/workflows/deploy.yml`, если там проверяется список `.pmtiles`). Данные и `pipeline/` не трогать.

## Контекст

`data/overlays.pmtiles` (`z8–16`) содержит три векторных слоя на всю Москву без разрезов по районам:
- `source-layer: "redzones"` (`z8–11`: `trunk`, `primary`, `railway`; `z12+`: `+secondary`; `z13+`: полигоны `industrial`, `construction`);
- `source-layer: "poi"` (`z11–12`: `subway_entrance`, `rail_platform`; `z13+`: все категории POI);
- `source-layer: "trees"` (`z15–16`: точки деревьев `{size, tone}`).

Нужно перевести отрисовку красных зон, POI и деревьев на `overlays.pmtiles`, убрать по-районные подгрузки `.geojson` на скролле и **вернуть настоящий Neon (`line-blur` + сочное свечение + яркое ядро) с `z9` на обзор всей Москвы**.

## Что сделать в `web/app/map.tsx`

1. **Подключить векторный источник `overlays` (`pmtiles://...data/overlays.pmtiles`):**
   - Зарегистрировать PMTiles-источник `overlays` (`type: "vector"`, `url: "pmtiles://...data/overlays.pmtiles"`, `minzoom: 8`, `maxzoom: 16`) аналогично `basemap` и `buildings`.
2. **Перевести слои `redzones-*` на источник `overlays` (`source: "overlays"`, `source-layer: "redzones"`) и вернуть Neon с `z9`:**
   - `REDZONE_MIN_ZOOM = 9` (вместо `12`), `REDZONE_FILL_MIN_ZOOM = 13`.
   - `redzones-fill`: `source: "overlays"`, `"source-layer": "redzones"`, `minzoom: 13`, `filter: ["in", ["get", "kind"], ["literal", ["industrial", "construction"]]]`.
   - **`redzones-glow` (настоящий Neon с `line-blur`):**
     - `source: "overlays"`, `"source-layer": "redzones"`, `minzoom: 9`, `filter: ["in", ["get", "kind"], ["literal", ["road", "railway"]]]`;
     - `"line-color": palette.redzone`;
     - `"line-width": ["interpolate", ["linear"], ["zoom"], 9, 4.5, 12, 8, 14, 13, 17.5, 24]`;
     - `"line-blur": ["interpolate", ["linear"], ["zoom"], 9, 3.5, 12, 6, 14, 9, 17.5, 16]`;
     - `"line-opacity": ["interpolate", ["linear"], ["zoom"], 9, theme === "dark" ? 0.34 : 0.26, 14, theme === "dark" ? 0.32 : 0.24, 17.5, theme === "dark" ? 0.38 : 0.30]` (и синхронизировать при смене темы!).
   - **`redzones-core` / `redzones-road` / `redzones-rail`:**
     - перевести на `source: "overlays"`, `"source-layer": "redzones"`, `minzoom: 9`, фильтр линейных (`["==", ["get", "kind"], "road"]` и `["==", ["get", "kind"], "railway"]`), чтобы яркое неоновое ядро читалось от `z9` до `z16`.
3. **Перевести слои `poi-*` и `trees` на `overlays` и убрать подгрузки при скролле:**
   - Слои `POI_LAYER_DEFS` (`poi-metro`, `poi-rail`, `poi-school`, `poi-kindergarten`, `poi-park`, `poi-shop`) перевести на `source: "overlays"`, `"source-layer": "poi"`, `minzoom: 11` (для метро/ж-д) и `13` (для остальных).
   - Слой `trees` (`trees-crown` / `trees-shadow`) перевести на `source: "overlays"`, `"source-layer": "trees"`, `minzoom: 15`.
   - **Отключить на скролле (`moveend`/`zoomend`):**
     - убрать `map.on("moveend", schedulePoiRefresh)`, `map.on("zoomend", schedulePoiRefresh)`, `map.on("moveend", scheduleRedzoneRefresh)`, `map.on("zoomend", scheduleRedzoneRefresh)`;
     - в `refreshBasemapDecorations` больше **не вызывать `buildTreePoints`** (оставить только сбор `labelCandidates` для текстовых подписей улиц/парков/воды с `z >= 12.6`).
   - **Сохранить точечную подгрузку `ensurePoiNear(center)` и `ensureRedzonesForDistrict(district.id)` ТОЛЬКО внутри `selectById` (при клике на конкретный дом):**
     - они нужны только в памяти для расчёта координат `nearestPoi` (пилюли «Метро · 5 мин») и `nearestPosition` (красные пины «Магистраль · 90 м») у выбранного дома, без `source.setData` на слои карты!

## Проверка

- `tsc --noEmit`, `npm run build`, `npm run e2e`.
- Замер `?perf=1` и 3 скриншота DPR1 (`z10` обзор всей Москвы с неоновыми магистралями, `z13.5` панорама, `z16.6` выбранный дом).

## Отчёт (≤25 строк)

1. `git diff --stat`.
2. Замеры FPS / p95 на `z10`, `z13.5`, `z16.6`.
3. Статус `tsc`, `build`, `e2e` и пути к 3 кадрам в `web/.shot/`.
