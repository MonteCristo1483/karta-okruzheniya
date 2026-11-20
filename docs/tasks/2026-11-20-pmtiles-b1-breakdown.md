# Задача B1: breakdown из тайлов в routes/<id>.json

Дата: 2026-11-20. Продолжение PMTiles, шаг B1. Клиент не трогаем,
ветку `data` не трогаем, из PostGIS полный экспорт не гоняем.

## Зачем
Замер шага A показал: `breakdown` в тайлах — 64% веса (761 КБ → 2,10 МБ),
а нужен он только для выбранного дома. Переносим его в файл, который и так
грузится при выборе дома (`routes/<id>.json`), и тайлы становятся в 2,8 раза
легче.

## Прочитать
1. `docs/research/pmtiles-plan.md` (раздел «Факт замера»)
2. `docs/STATE.md`
3. `pipeline/export_map_data.py` — строки ~100–165 (как пишутся `routes/`)
4. `pipeline/validate_export.py` — только `check_routes` (~379–400)

Не читай `*.geojson` целиком, `web/app/*`, `dist/`, `node_modules/`.

## Задача
1. **Экспорт для будущих прогонов.** В `export_map_data.py` при записи
   `routes/<id>.json` добавлять поле `breakdown` — тот же объект, что у дома
   в `presnya_buildings.geojson`. Ключ верхнего уровня, рядом с `categories`.
2. **Разовый мерж без PostGIS.** Новый `pipeline/add_breakdown_to_routes.py`:
   вход — каталог выгрузки (`--data-dir`, по умолчанию `web/public/data`),
   читает `presnya_buildings.geojson` + `routes/*.json`, дописывает
   `breakdown` в каждый файл маршрутов, идемпотентно (повторный запуск не
   меняет байты), печатает: сколько файлов обновлено, сколько без breakdown,
   суммарный прирост в байтах.
3. **Прогон на текущей выгрузке.** Если в `karta-pipeline` нет
   `web/public/data` — скопировать актуальный каталог из
   `C:\Users\starg\karta-web\web\public\data\` (gitignored). Затем:
   - `add_breakdown_to_routes.py --data-dir <каталог>`;
   - `python pipeline/validate_export.py <каталог>` — ждём 0 критичных;
   - контроль: у дома `64091236` `breakdown` в routes-файле побайтово
     совпадает по значениям с geojson; ещё 4 дома на выбор.

## Что нельзя
- Менять `pipeline/scoring.py`, `config/scoring.json`, `web/app/*`,
  workflows, `sw.js`, `build_tiles.py`.
- Коммитить геоданные и `*.pmtiles`; трогать ветку `data`, собирать `data.zip`,
  пушить в `main`/`gh-pages`.
- Гонять `export_map_data.py` из PostGIS — не нужно, мерж офлайновый.

## Ветка
```powershell
cd C:\Users\starg\karta-pipeline
git fetch origin
git switch -c task/pmtiles-b1 origin/main
```

## Отчёт
1. Что изменено (2 файла).
2. Сколько routes-файлов получили `breakdown`, сколько без (ожидаем 3306/3306).
3. Прирост веса: на файл и суммарно; вывод валидатора (коротко).
4. Сверка 5 домов: совпало/нет.
5. PR. Релиз данных (`data.zip`) НЕ делать — это отдельный шаг хаба.

## Критерий
- Валидатор: 0 критичных.
- Все routes-файлы содержат `breakdown`, совпадающий с geojson.
- Повторный прогон мержа не меняет файлы; клиент и данные в проде не тронуты.
Не более 25 минут.
