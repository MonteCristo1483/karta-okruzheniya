# Задача A: PMTiles — замер на реальных данных (прод не меняется)

Дата: 2026-11-20. Этап 2 роадмапа, первый шаг. **Клиент не трогаем.**
Ветка `data`, `config/scoring.json`, `export_map_data.py` — не трогать.

## Прочитать
1. `docs/research/pmtiles-plan.md`
2. `docs/STATE.md`
3. `pipeline/export_map_data.py` — только формат `presnya_buildings.geojson`
   (поля properties, как пишется `breakdown`)

Не читай `*.geojson` целиком, `web/app/*`, `dist/`, `node_modules/`,
`docs/research/*` кроме плана выше.

## Задача
Сделать воспроизводимый скрипт `pipeline/build_tiles.py`, который из
экспортированного geojson собирает тайлы и компактный индекс, и **измерить
реальные цифры**. Ничего не подключаем к сайту: артефакт пока никто не читает.

1. **Вход**: `web/public/data/presnya_buildings.geojson` (если файла нет в
   `karta-pipeline` — скопировать актуальный из `C:\Users\starg\karta-web\web\public\data\`,
   он gitignored; не запускай полный экспорт из PostGIS ради этого).
2. **Тайлы**: `buildings-<sha8>.pmtiles` в `web/public/data/tiles/`:
   `-Z12 -z16 -d13 --drop-densest-as-needed --no-tile-stats -l buildings`,
   id фичи = `properties.id` (`--use-attribute-for-id=id`), атрибуты
   `id`, `index`, `height`, `address`, `year`.
   `breakdown` — объект, MVT объекты не умеет: положить строкой JSON и
   проверить, что не ломается; если вес неприемлем — описать вариант
   «breakdown в `routes/<id>.json`» в отчёте.
3. **Индекс**: `web/public/data/buildings-index.json` — массив
   `[id, lon, lat, address, index, year]`, без геометрии.
4. **Проверка корректности**: скрипт сравнивает 5 контрольных домов
   (например 64091236 и ещё 4 любых) с исходным geojson по
   id/index/height/address/year — расхождений быть не должно.

## tippecanoe (Windows + Docker)
В apt его нет, готовых бинарников в релизах нет. Разрешённые пути:
- быстрый: `docker run --rm -v "${PWD}:/work" -w /work ghcr.io/felt/tippecanoe --help`
  — если образ доступен, работать через него;
- надёжный: собрать в контейнере `debian:bookworm`
  (`git clone --branch 2.79.0 https://github.com/felt/tippecanoe.git`, `make -j && make install`),
  версию пина зафиксировать в комментарии скрипта.
Если ни один путь не работает за 15 минут — стоп и отчёт в хаб, не изобретай
свой формер тайлов.

## Что нельзя
- Менять `pipeline/export_map_data.py`, `config/scoring.json`, `web/app/*`,
  `.github/workflows/*`, `web/public/sw.js`.
- Коммитить `*.pmtiles`, geojson и `buildings-index.json` (артефакты).
- Пушить в `main`/`data`/`gh-pages`, force-push, `npm run deploy`.

## Ветка
```powershell
cd C:\Users\starg\karta-pipeline
git fetch origin
git switch -c task/pmtiles-a origin/main
```
(worktree сейчас на старой `lane/pipeline` — переключение обязательно.)

## Отчёт (главное — цифры)
1. Размер `.pmtiles` (и gzip), число тайлов, размер индекса (и gzip).
2. Время сборки тайлов и общий вес артефактов.
3. Средний/максимальный размер тайла на z15 (проверить обещание
   «120–260 КБ первой загрузки»).
4. Сверка 5 контрольных домов: совпало/нет.
5. `breakdown` в тайлах: помещается или уходит в routes.
6. Что осталось сделать для клиента (B) и деплоя (C).
7. PR.

## Критерий
- `build_tiles.py` воспроизводим: повторный прогон даёт тот же размер ±5%.
- 5 контрольных домов совпадают с geojson.
- Прод не изменился: ни `web/app`, ни workflows, ни `data` не тронуты.
Не более 40 минут (Docker-сборка tippecanoe — самая долгая часть).
