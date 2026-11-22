# Задача V4b2c1: простая дневная дымка у верхней границы карты

Дата: 2026-11-22. Модель: **DeepSeek Flash** (механика по точному брифу).

Это один маленький шаг сцены после принятых V4b2a и V4b2b-fix. Старый общий
бриф `2026-11-21-visual-v4b2-scene.md` целиком **не выполнять**.

Ориентир: только `design/card-diorama-day.png`. Тёмная тема и вечерний закат
не меняются: вечер — отдельная V4c.

## Цель

Добавить ощущение воздуха у дальних верхних кварталов одним CSS-градиентом.
Дымка должна быть мягкой и почти незаметной: верх карты теряет немного
контраста, ближние дома/выбранный дом и весь UI остаются чёткими.

Не нужен физически точный туман, DOF, blur, sky/custom layer или новая
геометрия. Не менять цвет любых домов и подложки.

## Что уже есть

- `web/app/map.tsx`: контейнер `map-gradient map-grade`, затем sibling
  `map-vignette`, после которых идут LayersPanel/SearchBox/UI.
- `web/app/globals.css`: `.map-grade`, `.map-vignette` и dark override.
- `map-vignette` имеет `z-index: 1`; haze можно разместить до vignette с тем же
  `pointer-events: none`, не повышая z-index UI.

Разрешены изменения **только в этих двух файлах** и только добавление
`map-haze` + CSS.



## Обязательный стартовый вариант

Добавить `map-haze` между canvas-контейнером и `map-vignette`:

```text
light only:
height: 96 px desktop; 72 px mobile (<768 px)
z-index: 1
pointer-events: none
aria-hidden
```

Градиент сверху вниз:

```text
0%   rgba(227, 235, 244, 0.32)
45%  rgba(227, 235, 244, 0.14)
100% rgba(227, 235, 244, 0.00)
```

В dark: `display: none`. Не включать эту светлую плёнку в тёмной теме.
Никаких `position: fixed`, `inset: 0` с непрозрачным цветом за пределами haze,
HTML-таблиц пикселей или фильтров canvas.

Допустима максимум **одна поправка после before/after DPR 1**:

- total alpha (0.32 → одинаково масштабировать оба непрозрачных стопа)
  в пределах **0.24–0.38**;
- height desktop **80–120 px**, mobile **60–90 px**.

Третий вариант, blur и изменение vignette запрещены. Если простой градиент
выглядит полосой/молоком, отдать baseline/variant и остановиться.

## Что нельзя

- Менять `MAP_LIGHT`, крыши/фасады V5-V4b2a, окна, reveal #144, задержку #45,
  `map-vignette`, `map-grade`, землю, воду, парки, дороги и карточку.
- Добавлять MapLibre sky, setSky/fog/setFog, custom layer, `backdrop-filter`,
  blur/pseudo-DOF, слои, зависимости, ассеты и запросы.
- Менять scoring, данные, PMTiles, маршруты, изохроны и контур z18.
- Трогать `main`/`gh-pages`, force-push, `npm run deploy`, GitHub settings.
- Обновлять STATE/DEBT/decisions.

## Ветка и чтение

```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/visual-v4b2c1-haze origin/main
```

Если брифа в новой ветке нет — стоп и отчёт.

Прочитать только:
1. этот бриф;
2. `docs/STATE.md`;
3. точечные JSX map overlays и CSS map-grade/vignette по grep
   `map-haze|map-grade|map-vignette`;
4. `design/card-diorama-day.png`;
5. baseline `C:\Users\starg\karta-review\v4b2b-fix\overview-desktop-light-dpr1.png`.

## Проверка и Deploy

```powershell
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
npm run build
npm run e2e
git push -u origin task/visual-v4b2c1-haze
```

Дальше autopilot → PR → merge → Deploy. После Deploy проверить PMTiles и
`?tiles=0`: одна и та же haze, выбранное тело/окна/UI работают. Ошибок
консоли нет. #43/#44/#45 не исправлять.

### 8 финальных WebKit-кадров

Каждая сцена DPR 1 **и** DPR 2, явный `deviceScaleFactor`:

| Сцена | Размер | Тема | Камера | DPR |
|---|---|---|---|---|
| Обзор desktop light | 1290×900 | light | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Обзор desktop dark | 1290×900 | dark | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Выбранный дом mobile light | 390×844 | light | `?building=64091236`, z16.6 | 1, 2 |
| Выбранный дом mobile dark | 390×844 | dark | `?building=64091236`, z16.6 | 1, 2 |

Дополнительно baseline/final desktop light z15.35 DPR 1 на одном кадре для
проверки верхних ~96 px и неизменного центра. Финал — в
`C:\Users\starg\karta-review\v4b2c1-haze\`, бинарники не коммитить.

FPS на headless WebKit не меряем. Никакого blur/canvas изменения нет; если
для простого градиента потребуется производительный замер, это сигнал остановить
и упростить, а не добавлять эффекты.

## Критерий приёмки

- Верхние дальние кварталы стали чуть мягче, ориентир ~10–15% потери контраста,
  без видимой прямой полосы/шва на 96/72 px.
- Центр и низ карты не выбелены, крыши и пять ступеней различимы, теневые
  фасады V4b2b-fix хуже не стали.
- Search, chips, зум, карточка, POI-подписи читаются и кликаются как раньше;
  haze не перехватывает события.
- Dark-кадры и вечерняя тема без молочной плёнки; изменений dark почти нет.
- Выбранный дом не исчезает, окна работают, DPR 1/2 обе сцены корректны.
- Diff только `map.tsx` + `globals.css` для map-haze; 4 e2e зелёные.

## Таймбокс

**25 минут работы** плюс Deploy. Через 15–20 минут без корректного overlay —
стоп и отчёт. Не расширять задачу до материалов/V4b7/V4c.

## Отчёт

1. PR, Deploy, commit, diffstat и строчки haze.
2. Стартовый/итоговый alpha и heights, почему потребовалась поправка (если была).
3. `tsc`, build, e2e, PMTiles/fallback.
4. Восемь кадров DPR 1/2 и baseline/final desktop light DPR 1.
5. Одна строка рисков. Не начинать V4b2c2/V4b7/V4c.
