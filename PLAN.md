# PLAN.md — задачи по неделям (0–2)

Формат: одна задача = один заход агента = один коммит после проверки человеком.
Не брать следующую задачу, пока не принята предыдущая.

## Неделя 0 (вручную, без агента)
- [x] Репозиторий, SPEC.md, CLAUDE.md, /design
- [ ] Эталонный набор: 30 домов Москвы, про которые точно известны плюсы/минусы
      (файл data/reference_set.md: адрес → что хорошо/плохо → ожидаемый балл)
- [ ] Выбрать рабочее название и проверить домен

## Неделя 1 — данные (репозиторий → агент)
- [x] T1. docker-compose с PostGIS (образ postgis/postgis, БД okrug, порт 5432)
- [x] T2. Скрипт pipeline/download_osm.py: скачать выгрузку OSM по Москве
      (geofabrik) в data/raw/moscow.osm.pbf; без коммита самого файла
- [x] T3. Скрипт импорта зданий: osmium → buildings геометрия + теги
      (height, building:levels, addr*) в PostGIS, таблица buildings
- [x] T4. Фолбэк высоты: height = building:levels × 3, где height нет. Вьюха
      buildings_with_height
- [x] T5. Импорт POI: schools, kindergartens, parks, subway entrances, shops
      из того же PBF в таблицу poi (type, geometry)
- [x] T6. Импорт дорог и ж/д: highway=* с классами trunk/primary/secondary,
      railway=* в таблицу linear (kind, class, geometry)
- [x] T7. Ограничить всё районом Пресненский (полигон в pipeline/presnensky.geojson)
- [x] T8. Аудит: SQL-запросы «сколько зданий без высоты/адреса», отчёт в
      pipeline/audit_presnya.md — человек читает и решает, чего не хватает

## Неделя 2 — скоринг
- [x] T9. config/scoring.json по SPEC.md разделу 3 (все числа из SPEC)
- [x] T10. pipeline/scoring.py: бонусы и штрафы по формуле, без правок чисел в коде
- [x] T11. Юнит-тесты scoring: 5–7 случаев, включая границы (ровно 5 мин,
      ровно 150 м, индекс у пола и потолка 0/10)
- [x] T12. Прогнать эталонный набор: выгрузка CSV (адрес → индекс → разбивка),
      человек сверяет с data/reference_set.md; расхождения → правим ТОЛЬКО конфиг
- [x] T13. Слои штрафов добиты: zones (industrial/construction) из OSM,
      subtype у POI, фильтр магазинов по subtype в конфиге, слои включены

## Недели 3–6 — карта (задачи распишем после недели 2)
Ядро первым: 3D-здания + выбор дома + подсветка/затемнение. Порядок — по SPEC.
