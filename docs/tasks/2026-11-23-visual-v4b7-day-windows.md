# Задача V4b7: дневной цвет hero-окон без белой сетки

Дата: 2026-11-23. Модель: **Gemini 4 Argon, effort High**.

Базовая дневная сцена принята: roofs #138, light #148, haze #150, water #152,
greenery #154, roads #156. Это последний маленький material pass дня.
Старый широкий бриф `2026-11-21-visual-v4b7-windows.md` целиком **не
выполнять**: балконы, витрины, пояски, новая группировка и изменение плотности
в текущую задачу не входят.

Ориентиры: `design/card-diorama-day.png` и принятые кадры V4b2c2c. Не менять
здание/факты ради арт-рендера. Вечер — V4c, скорость окон — backlog #45.

## Цель

Сделать hero-окна дня чуть темнее/спокойнее, чтобы они читались как тёплое
стекло на стене, а не как белая перфорация. Сохранить их видимость и
архитектурный ритм выбранного дома на DPR 1/2.

Diff — **одна строка** с `winColorLight` в генераторе hero-окон.

## Обязательная замена

Сейчас около `buildHeroWindowFeatures` / `targetLitRatio` есть:

```text
mixHex("#f2e3c8", "#ffe9c9", blendT)
```

Заменить на:

```text
mixHex("#d4b795", "#e3c7a5", blendT)
```

Ничего больше в этой функции не менять. `colorDark`, `isLit`, `litRatio`,
существующие 45–55% горящих, halo dark и все непрозрачности остаются.
Дневной light halo отсутствует как и раньше.

## Что нельзя

- Менять оконную геометрию, ширину/высоту, floor steps, LOD/размер px,
  количество/кластеры, `targetLitRatio`, cap 1200/2000, reveal #144 и #45.
- Менять `colorDark`, негорящие dark окна, opacity, `MAP_LIGHT`, roofs,
  фасады, подложку, haze/vignette/grade, selected roof/body, карточку и UI.
- Добавлять балконы, витрины, швы, пояски, квартирные группы и вымышленные
  данные/зависимости/ассеты.
- Менять scoring, данные, PMTiles, маршруты/изохроны, `main`/`gh-pages`.
- Трогать #43 (количество окон), GitHub settings или документацию STATE/DEBT/
  decisions. Не начинать V4c.

## Ветка и чтение

```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/visual-v4b7-day-windows origin/main
```

Если брифа нет — стоп и отчёт.

Прочитать только:
1. этот бриф;
2. `docs/STATE.md`;
3. точечный блок `winColorLight/winColorDark` и `syncSelectedWindowTheme`;
4. `design/card-diorama-day.png`;
5. baseline светлые кадры `C:\Users\starg\karta-review\selection-continuity\
   final/webkit/desktop-light-dpr1.png` и `mobile-light-dpr1.png`.

## Проверка

```powershell
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
npm run build
npm run e2e
git push -u origin task/visual-v4b7-day-windows
```

Дальше autopilot → PR → merge → Deploy. После Deploy проверить PMTiles и
`?tiles=0`: выбранное тело остаётся видно до reveal #144, крыша/окна/UI
работают, ошибок консоли нет. Разницу числа окон #43 только зафиксировать.

### Обязательные кадры WebKit

Каждая сцена обязательно DPR 1 **и** DPR 2 с явным `deviceScaleFactor`:

| Сцена | Размер | Тема | Камера |
|---|---|---|---|
| Selected desktop light | 1290×900 | light | `?building=64091236`, z16.6 |
| Selected desktop dark | 1290×900 | dark | тот же дом/камера |
| Selected mobile light | 390×844 | light | тот же дом/камера |
| Selected mobile dark | 390×844 | dark | тот же дом/камера |
| Близкий дом desktop light | 1290×900 | light | `?building=64091248`, z18 |
| Второй дом mobile light | 390×844 | light | `?building=64091248`, z16.6 |

Итого **12 кадров** (6 сцен × DPR 1/2). Для каждой сцены baseline/final нужен
одинаковый дом/камера. Сохранить в `C:\Users\starg\karta-review\v4b7-day-windows\`,
бинарники/скрипты не коммитить.

На основном доме измерить хотя бы 3 одинаковые точки `window → соседняя wall`
baseline/final и указать Rec.709 luma/контрастное соотношение. Не подгонять
цвета под чужие дома или менять selected step. FPS не меряем, задержку #45
не оптимизировать.

Вторая палитра допускается только при явном блокере, максимум одна поправка
этих же двух light RGB-токенов ±10 со сравнением. Третья — стоп и отчёт.

## Критерий приёмки

- Днём окна остаются различимыми, но больше не образуют почти белую сетку.
  Тёплое стекло спокойнее встроено в фасад; стена/крыша/silhouette первичны.
- Контраст window/wall ниже baseline без превращения в невидимые точки;
  одинаковые DPR 1/2 кадры не дают нового алиасинга/LED-микросетки.
- Сцена/high-score и mid-score выбранного дома сохраняет яркую ступень;
  пять ступеней обычного города и материалы сцены не изменены.
- Dark кадры/янтарь/зелёное свечение не изменились.
- #144 соблюдён: нет исчезновения тела; на PMTiles/fallback одинаковый
  визуальный материал и работающий selected house.
- Diff только `winColorLight` одной строкой; `tsc`, build, 4 e2e зелёные.

## Таймбокс

**35 минут работы** плюс Deploy. Через 20–25 минут без корректного 12-кадрового
сравнения — стоп и точный отчёт. Не делать V4c и не возвращаться к ритму.

## Отчёт

1. PR, Deploy, commit и diffstat одной строки.
2. Итоговые `winColorLight`, три Rec.709 контраста window/wall baseline/final.
3. `tsc`, build, e2e, PMTiles/fallback.
4. 12 кадров DPR 1/2 и одно baseline/final резюме по дням.
5. Одна строка рисков. Не начинать V4c и не менять ритм/скорость окон.
