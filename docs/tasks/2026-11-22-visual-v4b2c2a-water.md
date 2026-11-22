# Задача V4b2c2a: спокойный бирюзовый материал воды

Дата: 2026-11-22. Модель: **DeepSeek Flash** (точные два токена).

После V4b2c1 haze делаем только воду дневной сцены. Зелень, дороги, вечер,
закат и все остальные материалы — отдельные задачи.

Ориентир: исходный `design/card-diorama-day.png`. Не копировать художественные
отражения, лодки или объектов, которых нет в OSM.

## Цель

Сделать воду чуть более бирюзовой и отделённой от светлой земли/дорог, не
добавляя неон, волновой шум и новую геометрию. Diff должен состоять ровно из
двух изменений light-палитры в `web/app/map.tsx`.

## Обязательные изменения

```text
MAP_THEMES.light.water:     #4fbfe6 → #35b8d0
MAP_THEMES.light.waterLine: #2f9fc8 → #248fa9
```

Эти токены уже используются в `bm-water`, `bm-river`, `bm-waterway` через
`bmPaint`/style updates. Новых токенов для dark/ВОДЫ и новых слоёв не создавать.
Все остальные цвета и свойства оставить как есть.

## Что нельзя

- Менять dark `water/waterLine`, геометрию OSM, PMTiles, источники basemap,
  `fill-outline-color`, ширины, `line-blur`, opacity или порядок слоёв.
- Добавлять fill-pattern, текстуры волн, отражения домов, SVG/PNG, береговые
  буферы, лодки и любые отсутствующие в OSM объекты.
- Менять зелень, дороги, землю, крыши, фасады, MAP_LIGHT, haze/vignette/grade,
  окна, reveal #144, карточку, маршруты и изохроны.
- Менять scoring, данные, зависимости, `main`/`gh-pages`; force-push,
  `npm run deploy`, GitHub settings.
- Обновлять STATE/DEBT/decisions.

## Ветка и чтение

```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/visual-v4b2c2a-water origin/main
```

Если брифа там нет — остановиться и сообщить.

Прочитать только:
1. этот бриф;
2. `docs/STATE.md`;
3. light/dark palette `water/waterLine` и точечные блоки `bm-water`, `bm-river`,
   `bm-waterway` в `web/app/map.tsx`;
4. `design/card-diorama-day.png`;
5. baseline `C:\Users\starg\karta-review\v4b2c1-haze\final-desktop-light-z1535-dpr1.png`.

## Проверка

```powershell
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
npm run build
npm run e2e
git push -u origin task/visual-v4b2c2a-water
```

Дальше autopilot → PR → merge → Deploy. После Deploy проверить PMTiles и
`?tiles=0`: одна палитра воды, выбранный дом и UI работают, ошибок консоли нет.
#43/#44/#45 не исправлять.

### 8 финальных WebKit-кадров

Каждая сцена DPR 1 **и** DPR 2 с явным `deviceScaleFactor`:

| Сцена | Размер | Тема | Камера | DPR |
|---|---|---|---|---|
| Обзор desktop light | 1290×900 | light | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Обзор desktop dark | 1290×900 | dark | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Выбранный дом mobile light | 390×844 | light | `?building=64091236`, z16.6 | 1, 2 |
| Выбранный дом mobile dark | 390×844 | dark | `?building=64091236`, z16.6 | 1, 2 |

Дополнительно baseline/final desktop light z15.35 DPR 1 на одном кадре для
Москвы-реки, двух прудов и подписей. Сохранить в
`C:\Users\starg\karta-review\v4b2c2a-water\`, бинарники не коммитить.

FPS не измеряем: новых слоёв и геометрии нет. Поправок палитры не делать без
конкретного дефекта на этих кадрах; если цвет конфликтует с фактами/UI —
остановиться и дать before/after вместо самостоятельного третьего варианта.

## Критерий приёмки

- Днём река и пруды выглядят единым бирюзовым материалом, отличаются от земли,
  дорог и парков; не уходят в тёмно-синий/неоновый HUD.
- Берега и родная OSM-геометрия не изменились, малые waterways не выглядят
  вымышленными световыми трубами.
- Подписи воды/POI, маршрут и красные зоны читаются; haze и тени не ухудшились.
- Dark-кадры не изменились по палитре. Выбранное тело/окна и карточка работают.
- Diff только два light-токена в `map.tsx`; `tsc`, build и 4 e2e зелёные.

## Таймбокс

**25 минут работы** плюс Deploy. Через 15–20 минут без корректного before/after
или блокера проверки — стоп и отчёт.

## Отчёт

1. PR, Deploy, commit и diffstat двух токенов.
2. `tsc`, build, e2e, PMTiles/fallback.
3. Восемь кадров DPR 1/2 и baseline/final desktop light z15.35 DPR 1.
4. Одна строка рисков. Не начинать V4b2c2b/V4b7/V4c и не менять зелень/дороги.
