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
- [x] T8b. Школы с data.mos.ru: pipeline/import_schools_mosru.py → poi
      (osm_type='mosru', 475 школ; координаты из «Центроид», Nominatim — фолбэк)

## Неделя 2 — скоринг
- [x] T9. config/scoring.json по SPEC.md разделу 3 (все числа из SPEC)
- [x] T10. pipeline/scoring.py: бонусы и штрафы по формуле, без правок чисел в коде
- [x] T11. Юнит-тесты scoring: 5–7 случаев, включая границы (ровно 5 мин,
      ровно 150 м, индекс у пола и потолка 0/10)
- [x] T12. Прогнать эталонный набор: выгрузка CSV (адрес → индекс → разбивка),
      человек сверяет с data/reference_set.md; расхождения → правим ТОЛЬКО конфиг
- [x] T13. Слои штрафов добиты: zones (industrial/construction) из OSM,
      subtype у POI, фильтр магазинов по subtype в конфиге, слои включены
- [x] T12b. Сверки после T13: v2 (зоны + subtype) 15/30 в прогнозе;
      v3 (калибровка магистрали 0.85/0.55/200 м + правки эталона) 18/30,
      «хорошие» 10/10. scoring.json заморожен до Valhalla-маршрутов,
      правки только после них

## Недели 3–6 — карта
Ядро первым: 3D-здания + выбор дома + подсветка/затемнение. Порядок — по SPEC.

- [x] M1. Каркас web/ (Next.js + TS strict + Tailwind) + MapLibre: диорама
      Пресненского без подложки, fill-extrusion зданий по высоте, цвет по
      индексу (<5 красный, 5-7 нейтральный, >7 зелёный, пастель). Данные:
      pipeline/export_map_data.py → web/public/data/presnya_buildings.geojson
- [x] M2. Выбор дома: клик → подсветка выбранного, затемнение остального
- [x] M3. Шторка карточки дома: адрес, год, индекс 7.5/10, разбивка баллов
- [x] M4. Слои-чекбоксы (школы, сады, парки, метро, магазины), маршруты,
      красные зоны, «подводные камни»
- [x] M5a. Тёмная тема: design tokens, переключатель, localStorage
- [x] M5b. «Отправить клиенту»: публичная ссылка /?building=<osm_id>,
      статический экспорт на GitHub Pages (деплой: npm run deploy, см. DEPLOY.md)
- [x] M6. Адаптив desktop: шторка сбоку, здание слева/справа от неё
- [x] M7. UX и визуал мобильной карты: шторка со snap, дуги маршрутов,
      splash screen, PWA-визуал, тень зданий

## V-блок — пешие маршруты Valhalla (приоритет CTO 12.11: блокирует
## и визуал маршрутов, и разморозку калибровки скоринга)
Тайлы: data/processed/valhalla (3.5 ГБ, в git не коммитится, сборка ~12 мин).
Сервис: docker run -d --name valhalla-svc \
  -v $PWD/data/processed/valhalla:/custom_files -p 8002:8002 \
  ghcr.io/valhalla/valhalla:latest valhalla_service /custom_files/valhalla.json
- [x] V1. Тайлы Москвы собраны, valhalla_service поднят локально (без VPS)
- [x] V2. pipeline/routes_valhalla.py: таблица routes — пеший маршрут
      здание → ближайший POI каждой категории, 16 530 строк
- [x] V3. Экспорт маршрутов на фронт (per-building JSON) + реальные линии
      вместо квадратичных Безье
- [x] V4. Скоринг по времени маршрута вместо прямой + перекалибровка
      config/scoring.json: v4 (маршруты + ж/д платформы МЦД) — эталон 21/30
- [x] V5. Эталонная проверка v4: 21/30 (было 18/30), конфиг разморожен;
      дальнейшая подгонка отклонена (переобучение, см. decisions.md 13.11)
