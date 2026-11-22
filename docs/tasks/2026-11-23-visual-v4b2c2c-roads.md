# Задача V4b2c2c: тёмная дорожная графика по существующей геометрии

Дата: 2026-11-23. Модель: **DeepSeek Flash** (пять RGB-значений).

После принятых haze/воды/зелени меняем только light-палитру дорог. Никаких
новых полос, объектов, road casing, lane geometry или расширения дорог.
Это последний отдельный material pass перед решением хаба о V4b7.

Ориентир: исходный `design/card-diorama-day.png`.

## Цель

Уйти от почти белых невесомых дорог к сине-серому полотну с заметной осевой
разметкой на major. Сохранить функциональную читаемость магистралей и не
перевести общий вид в киберпанк/тёмную тему днём.

## Обязательные изменения в `web/app/map.tsx`

```text
MAP_THEMES.light.roadMinor:       #fbfdff → #8a99ab
MAP_THEMES.light.roadCasing:      #aebfd2 → #6d7f94
MAP_THEMES.light.roadMajor:       #ffffff → #53657d
MAP_THEMES.light.roadMajorCasing: #9fb2c8 → #2f435f
MAP_THEMES.light.roadMark:        #d6e2ef → #e8edf1
```

Существующие слои `bm-road-minor-casing`, `bm-road-minor`, `bm-road-casing`,
`bm-road-major`, `bm-road-marking` и `bmPaint` использовать без изменений.
Их widths/dash/opacity/line caps/geometry не менять. Dark road tokens не менять.

## Что нельзя

- Менять widths, dasharray, opacity, cap/join, blur, filter и порядок слоёв.
- Создавать отдельные полосы, разметку на minor roads, новые дороги или буферы.
- Менять парк/деревья (#154), воду (#152), землю, дома, MAP_LIGHT,
  haze/vignette/grade, окна, reveal #144, маршруты, красные зоны и карточку.
- Менять scoring, данные, PMTiles, зависимости, основной/тёмный режим и UI.
- Трогать `main`/`gh-pages`, force-push, `npm run deploy`, GitHub settings.
- Обновлять STATE/DEBT/decisions или начинать V4b7/V4c.

## Ветка и чтение

```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/visual-v4b2c2c-roads origin/main
```

Если брифа там нет — стоп и отчёт.

Прочитать только:
1. этот бриф;
2. `docs/STATE.md`;
3. road tokens и пять дорожных layer-блоков в `web/app/map.tsx`;
4. `design/card-diorama-day.png`;
5. baseline `C:\Users\starg\karta-review\v4b2c2b-greenery\final-desktop-light-z1535-dpr1.png`.

## Проверка

```powershell
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
npm run build
npm run e2e
git push -u origin task/visual-v4b2c2c-roads
```

Дальше autopilot → PR → merge → Deploy. После Deploy проверить PMTiles и
`?tiles=0`, выбор дома, подписи/маршрут/красные зоны и отсутствие ошибок.

### 8 финальных WebKit-кадров

Каждая сцена DPR 1 **и** DPR 2 с явным `deviceScaleFactor`:

| Сцена | Размер | Тема | Камера | DPR |
|---|---|---|---|---|
| Обзор desktop light | 1290×900 | light | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Обзор desktop dark | 1290×900 | dark | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Выбранный дом mobile light | 390×844 | light | `?building=64091236`, z16.6 | 1, 2 |
| Выбранный дом mobile dark | 390×844 | dark | `?building=64091236`, z16.6 | 1, 2 |

Дополнительно baseline/final desktop light z15.35 DPR 1 на одном кадре:
ТТК/магистрали, локальные улицы, река, парк и подписи.
Сохранить в `C:\Users\starg\karta-review\v4b2c2c-roads\`, бинарники и
служебные скрипты не коммитить. FPS не меряем.

Палитру не подбирать дополнительно без одного конкретного блокера. Если
магистраль или местная улица становится неотличимой от красной зоны/маршрута
или дорога перекрывает факты — стоп и baseline/final.

## Критерий приёмки

- Major читается как тёмное сине-серое полотно со светлой пунктирной осевой;
  minor заметны, но спокойнее major; дорожная иерархия видна в DPR 1 и 2.
- Магистраль/красная зона и mint route не сливаются; подписи и POI читаемы.
- Дома/крыши/зелень/вода/haze не ухудшились. Сцена остаётся дневной пастельной
  диорамой, а не ночной/неоновой картой.
- Dark-кадры не изменились; выбор дома/окна/шторка работают.
- Diff только пять light-токенов в `map.tsx`; `tsc`, build, 4 e2e зелёные.

## Таймбокс

**25 минут работы** плюс Deploy. Стоп через 15–20 минут без корректного diff
или кадра.

## Отчёт

1. PR, Deploy, commit и diffstat пяти токенов.
2. `tsc`, build, e2e, PMTiles/fallback.
3. Восемь кадров DPR 1/2 и baseline/final desktop light z15.35 DPR 1.
4. Одна строка рисков. Не начинать V4b7/V4c самостоятельно.
