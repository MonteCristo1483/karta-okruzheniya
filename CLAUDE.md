# CLAUDE.md — правила работы агента в этом репозитории

## Разрешения (permissions)
РАЗРЕШЕНО без подтверждения: npm install, npm run build/dev/deploy/start, npx, python, pip install, docker compose, git add/commit/push, создание/правка/удаление файлов внутри репозитория. СПРАШИВАТЬ: rm -rf системных папок, curl | bash, отправка данных на внешние сервисы, действия за пределами папки проекта.

## Проект
«Карта окружения дома» — см. SPEC.md (обязателен к прочтению перед задачей).
Макеты — в /design. Отвечать по-русски.

## Стек (фиксирован, не менять без запроса человека)
- Фронтенд: Next.js + TypeScript (strict) + Tailwind, mobile-first, PWA.
- Карта: MapLibre GL JS (3D-экструзия зданий) + deck.gl (маршруты, зоны).
- Тайлы: PMTiles (tippecanoe), подложка Protomaps, без платных API карт.
- Пайплайн данных: Python (geopandas, osmium), PostGIS.
- Маршруты: Valhalla (позже, VPS).
- Хостинг данных: статика + CDN. Бэкенд позже: Supabase или Postgres в РФ.

## Железные правила
1. Одна маленькая задача за раз. После каждой: человек проверяет и коммитит.
2. Скоринг: формула ТОЛЬКО в config/scoring.json. Код меняет только формат,
   не числа. Любое изменение формулы = правка конфига + обновление тестов.
3. Юнит-тесты на скоринг обязательны и должны проходить.
4. Версии зависимостей фиксируются (package.json без ^ и ~). Перед использованием
   API библиотеки — свериться с официальной документацией, не выдумывать API.
5. Не добавлять фичи вне SPEC.md раздела «MVP-фичи».
6. Терминология строго из SPEC.md раздела 2 («Индекс окружения»).
7. Перед «готово» — прогнать тесты и линтер.
8. Не коммитить большие бинарники (data/raw, *.pmtiles) — см. .gitignore.

## Структура репозитория (целевая, создавать по мере задач)
- /design — макеты (не трогать)
- /pipeline — Python-пайплайн данных (OSM → PostGIS → scoring → PMTiles/JSON)
- /web — Next.js-фронтенд
- config/scoring.json — все коэффициенты индекса
- data/ — выгрузки (сырые не коммитятся)
- docs/decisions.md — журнал ключевых решений и находок

## Журнал решений
Каждая значимая находка/решение — строка в docs/decisions.md (формат: дата — решение — почему).

## Команды
- Пайплайн: зависимости — `pipeline/.venv/Scripts/python.exe -m pip install -r pipeline/requirements.txt`;
  импорты — `pipeline/.venv/Scripts/python.exe pipeline/import_buildings.py` (и import_poi, import_linear,
  extract_presnensky, scoring — по PLAN.md); БД поднимается `docker compose up -d`
- Фронтенд: (пока нет)
- Тесты: `pipeline/.venv/Scripts/python.exe -m unittest discover -s pipeline -p "test_*.py" -v`
