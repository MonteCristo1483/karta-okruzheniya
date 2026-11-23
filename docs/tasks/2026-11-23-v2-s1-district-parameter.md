# Задача V2-S1: район как параметр + is_home + три района

Дата: 2026-11-23. Исполнитель: pipeline-исполнитель (механика, без вкуса).
Полоса: **только `pipeline/` и `config/`**. `web/` не трогать.

## Контекст

Владелец заморозил косметику и выбрал масштабирование на всю Москву
(включая Некрасовку) на текущем стеке. Перед полным прогоном нужно убрать
одноместность Пресни из пайплайна и ввести разделение «здание на карте →
жилой дом для расчёта».

Ничего не публикуем: прод, ветка `data`, `/r/`, релизы и хостинг — не эта задача.

## Цель (три результата, больше ничего)

1. Пайплайн собирает **любой район** по id/имени: никаких `*_presnya` вьюх,
   `presnensky` в коде, зашитого relation id.
2. У здания есть флаг `is_home`. Всё, что не жилое, **остаётся на карте**, но
   не получает балл, маршруты и изохрону.
3. Локально собраны три района — **Пресненский, Тверской, Арбат** — с
   фактическими размерами артефактов и оценкой на 125 районов.

## Что менять

### 1. Хранение тегов (`pipeline/import_buildings.py`)

Сейчас в `buildings` только `osm_id, geometry, height, building_levels,
addr_street, addr_housenumber` — классифицировать нечем. Добавить:

- `tags JSONB` — весь набор тегов area (компактно, без фильтрации);
- `is_home BOOLEAN NOT NULL DEFAULT TRUE` — вычисляется при импорте.

`ON CONFLICT (osm_id) DO UPDATE` должен обновлять теги и флаг, а не
пропускать существующие строки. Пробный прогон — по текущему
`data/raw/moscow.osm.pbf` (это региональная выгрузка, ~66 с; **новый PBF не
скачивать**, это не задача шага 1).

### 2. Правило `is_home` (`config/buildings.json` + `pipeline/buildings_filter.py`)

Логика: **по умолчанию жилое**, если есть `addr:street` и `addr:housenumber`.
Понижаем до нежилого только по явному признаку. Порога этажности нет —
иначе вырежется частный сектор Некрасовки.

`config/buildings.json` (новый файл, не `scoring.json`):

```json
{
  "require_address": true,
  "default_is_home": true,
  "non_home_building": [
    "garage", "garages", "garage_boxes", "shed", "hut", "roof", "carport",
    "construction", "ruins", "industrial", "warehouse", "factory",
    "manufacture", "service", "parking", "hangar", "barn", "stable",
    "greenhouse", "kiosk", "transformer_tower", "bridge", "canopy",
    "storage_tank", "water_tower", "silo", "tank", "container",
    "grandstand", "allotment_house", "retail", "commercial", "office",
    "hotel", "church", "school", "hospital"
  ],
  "non_home_tags": {
    "shop": ["mall"],
    "tourism": ["hotel", "hostel", "motel"],
    "amenity": ["place_of_worship", "school", "university", "college",
                "hospital", "clinic", "kindergarten"],
    "man_made": ["works", "gasometer", "silo", "storage_tank", "water_tower"],
    "power": ["plant", "substation"],
    "landuse": ["industrial", "railway"]
  }
}
```

Правило должно быть в одном месте (`buildings_filter.py`) и использоваться
импортом, экспортом, скорингом и выборкой для маршрутов. Никаких копий
списка в других файлах.

### 3. Район как параметр (`pipeline/migrations/004_district_views.sql`)

- Функции с параметром района вместо вьюх на одну строку `presnensky`:
  `district_buildings(d_id BIGINT)`, `district_poi(d_id BIGINT)`,
  `district_linear(d_id BIGINT)` — пересечение с `districts.geometry`.
- Старые `*_presnya` вьюхи **не удалять** до шага 2, но новые задачи на них
  не опираются.
- `pipeline/extract_presnensky.py` оставить как исторический скрипт, но
  пайплайн его больше не вызывает.

### 4. Сборка района (`pipeline/build_district.py`)

CLI: `--district <osm_id | имя>`, `--out data/districts/<osm_id>/`.
Делает то же, что сейчас делается руками для Пресни: здания (все, с
`is_home`), POI, красные зоны, `buildings-index` (только жилые), геометрию
района. То, что уже умеет `export_map_data.py` / `build_tiles.py`, —
переиспользовать, не копировать.

Маршруты и изохроны **не считать** (это шаг 2 и отдельный прогон Valhalla).
Для Пресни существующие файлы не перезаписывать.

### 5. Скоринг и маршруты — только жилые

- `scoring.py` считает балл только строкам `is_home = TRUE`.
- `routes_valhalla.py` и `isochrones_valhalla.py` в выборке кандидатов
  добавляют `AND is_home`. Прогон не запускать, только правка запроса.
- `config/scoring.json` **не менять**: ни одного числа, ни одной шкалы.

## Что нельзя

- Менять `web/`, карточку, палитру, `/r/`, деплой, ветку `data`, GitHub settings.
- Менять `config/scoring.json`, формулу или веса.
- Скачивать новый PBF, запускать полную Москву, считать маршруты/изохроны.
- Удалять `*_presnya` вьюхи и старые скрипты (сначала доказываем новый путь).
- Добавлять зависимости. Всё нужное уже есть.
- Коммитить данные (`data/`, `*.geojson`, `*.pmtiles` — в `.gitignore`).

## Проверка

Предусловие: `docker compose up -d` (PostGIS) и
`pipeline/.venv/Scripts/python.exe` установлен.

```powershell
# 1. Теги и флаг
pipeline/.venv/Scripts/python.exe pipeline/import_buildings.py

# 2. Все районы Москвы (admin_level=8)
pipeline/.venv/Scripts/python.exe pipeline/extract_districts.py

# 3. Три района
pipeline/.venv/Scripts/python.exe pipeline/build_district.py --district Пресненский
pipeline/.venv/Scripts/python.exe pipeline/build_district.py --district Тверской
pipeline/.venv/Scripts/python.exe pipeline/build_district.py --district Арбат

# 4. Регресс
pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v
git diff --stat
```

Тайлы (tippecanoe) для трёх районов — по возможности локально, чтобы снять
размер; если Docker-образ не поднят, размер зданий оценить по geojson и
указать это в отчёте.

## Критерий приёмки

1. Пресненский: **3 306** зданий с адресом (как сейчас), POI **243**.
   Число `is_home` меньше — это ожидаемо; привести точное.
2. Тверской и Арбат собираются **одной командой**, без правок кода.
3. В `districts` ≥ 125 районов Москвы. Если меньше — стоп и отчёт.
4. Маршруты/изохроны Пресни и её файлы **не изменились** побайтово.
5. `scoring.json` без диффа. 60 тестов зелёные. `web/` без диффа.
6. В отчёте — таблица: район, зданий, жилых, POI, размер geojson, размер
   тайлов (если собраны), размер индекса; и экстраполяция на 125 районов
   по среднему и по максимуму.

## Стоп-условия

- Расхождение по Пресне (≠3 306 адресных) — стоп, это регресс выборки.
- Приходится менять `web/`, скоринг или формулу — стоп.
- Для классификации не хватает данных в OSM у >2% зданий — зафиксировать,
  не додумывать.
- Больше 8 изменённых файлов или больше 90 минут работы — стоп и отчёт.

## Отчёт

1. PR, commit, `git diff --stat` (файлы, не строки целиком).
2. Таблица из п.6 приёмки + экстраполяция на 125 районов.
3. Что именно попало в `is_home`, что нет: 5–10 примеров адресов каждого типа
   (жилой, гараж, ТЦ, отель, промзона) — чтобы владелец проверил глазами.
4. Риски одной строкой: где правило ошибётся и что это будет стоить.
5. Не начинать упаковку, `/r/`, релизы и полный прогон — это шаги 2–3.
