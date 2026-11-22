# Задача V4b2c2b: более глубокий материал парков и реальных деревьев

Дата: 2026-11-22. Модель: **DeepSeek Flash** (четыре RGB-значения).

После принятых haze/воды меняем только зелень дневной сцены. Дороги — отдельная
V4b2c2c, вечер — V4c.

Ориентир: исходный `design/card-diorama-day.png`.

## Цель

Убрать бледную мятную зелень парков и сделать существующие OSM-полигоны/точки
деревьев чуть глубже и чётче. **Не добавлять, удалять или перемещать деревья**,
не строить кроны, буферы или новые объекты. Diff — только 4 light-токена.

## Обязательные изменения в `web/app/map.tsx`

```text
MAP_THEMES.light.park:     #7cc98f → #579b68
MAP_THEMES.light.wood:     #5fb47a → #428b58
MAP_THEMES.light.tree:     #5fb876 → #4a9960
MAP_THEMES.light.treeDark: #3e8e55 → #2f6f43
```

Все эти токены уже используются в текущем basemap/tree styles. Dark-версии
остаются прежними. Не менять `circle-radius`, `circle-stroke-width`, opacity,
blur, размеры иконок и любые LOD.

## Что нельзя

- Создавать деревья/спрайты/кроны по паркам, использовать пространственные
  буферы или другие новые источники/геометрию.
- Менять дороги, воду (#152 уже принята), землю, дома/крыши, MAP_LIGHT,
  haze/vignette/grade, окна, reveal #144, карточку и UI.
- Менять dark, scoring, данные, PMTiles и зависимости.
- Трогать `main`/`gh-pages`, force-push, `npm run deploy`, GitHub settings.
- Обновлять STATE/DEBT/decisions и начинать V4b2c2c/V4b7/V4c.

## Ветка и чтение

```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/visual-v4b2c2b-greenery origin/main
```

Если брифа там нет — остановиться и сообщить.

Прочитать только:
1. этот бриф;
2. `docs/STATE.md`;
3. light/dark palette `park/wood/tree/treeDark` и их style usage по grep
   `park|wood|tree`, без чтения `map.tsx` целиком;
4. `design/card-diorama-day.png`;
5. baseline `C:\Users\starg\karta-review\v4b2c2a-water\final-desktop-light-z1535-dpr1.png`.

## Проверка

```powershell
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
npm run build
npm run e2e
git push -u origin task/visual-v4b2c2b-greenery
```

Дальше autopilot → PR → merge → Deploy. После Deploy проверить PMTiles и
`?tiles=0`, выбранный дом, парк/деревья, POI и отсутствие ошибок консоли.

### 8 финальных WebKit-кадров

Каждая сцена DPR 1 **и** DPR 2:

| Сцена | Размер | Тема | Камера | DPR |
|---|---|---|---|---|
| Обзор desktop light | 1290×900 | light | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Обзор desktop dark | 1290×900 | dark | z15.35 / pitch 55 / bearing −25 | 1, 2 |
| Выбранный дом mobile light | 390×844 | light | `?building=64091236`, z16.6 | 1, 2 |
| Выбранный дом mobile dark | 390×844 | dark | `?building=64091236`, z16.6 | 1, 2 |

Дополнительно baseline/final desktop light z15.35 DPR 1 на одном кадре с
парком Красная Пресня, прудами и существующими точками деревьев.
Сохранить в `C:\Users\starg\karta-review\v4b2c2b-greenery\`, бинарники не
коммитить. FPS не меряем.

## Критерий приёмки

- Парк и wood стали глубже и отделены от крыш/земли, но не кислотно-зелёным
  HUD; существующие точки деревьев читаются как часть парка, не как новые факты.
- Подписи парка/POI, дома, пять крыш, красные зоны, маршрут и вода остаются
  различимыми; количество/размер/координаты деревьев не меняются.
- Dark не изменился, haze/тени/selected/окна не регрессировали.
- Diff только 4 light-токена в `map.tsx`; `tsc`, build, 4 e2e зелёные.

Если цвет на DPR 1 конфликтует с POI или напоминает HUD — остановиться и дать
baseline/final, не начинать самостоятельный подбор или добавление крон.

## Таймбокс

**25 минут работы** плюс Deploy. Стоп через 15–20 минут без корректного diff
или кадра.

## Отчёт

1. PR, Deploy, commit и diffstat четырёх токенов.
2. `tsc`, build, e2e, PMTiles/fallback.
3. Восемь кадров DPR 1/2 и baseline/final desktop light z15.35 DPR 1.
4. Одна строка рисков. Не начинать дороги/V4b7/V4c.
