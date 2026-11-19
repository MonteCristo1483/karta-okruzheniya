# HANDOFF — старт новой сессии

Дата: 2026-11-19. Проект: «Карта окружения дома».
Этот файл — точка входа. Читать вместе с `CLAUDE.md`, `SPEC.md`,
`docs/ROADMAP.md`, `docs/decisions.md`, `docs/SECURITY.md`,
`docs/AGENT_GRID.md`.

## 0. Готовый промпт для нового окна

Скопируй в новую сессию и добавь задачу в конце:

```text
Ты — CTO и инженер проекта «Карта окружения дома». Прочитай HANDOFF.md,
CLAUDE.md, SPEC.md, docs/ROADMAP.md, docs/decisions.md, docs/SECURITY.md,
docs/AGENT_GRID.md.

Работаем по одной маленькой задаче за раз. Не пушь в main напрямую: main
защищён, нужен branch → PR → CI → merge. Не коммить и не деплой без
человека. Скоринг меняется только через config/scoring.json. Отвечай
по-русски, объясняй простыми словами: владелец не программист.

Текущая задача: <вставь задачу>.
```

## 1. Что это

B2C PWA для людей, которые выбирают или снимают квартиру в Москве.
Человек находит дом, видит «индекс окружения» 0–10 и, главное, конкретные
факты: магистраль, ж/д, стройка, промзона, время до метро/школы/парка.
Цель продукта: **за 2 минуты понять, что с домом не так**, а не получить
«красивую оценку».

Сейчас один район — Пресненский. Масштабирование на 30+ районов отложено
до PMTiles и онлайн-пайплайна.

- Публичный сайт: https://montecristo1483.github.io/karta-okruzheniya/
- Репозиторий: https://github.com/MonteCristo1483/karta-okruzheniya
- Локально: `C:\Users\starg\OneDrive\Документы\GitHub\karta-okruzheniya`

## 2. Состояние на 2026-11-19

Работает:

- 3D-карта Пресни (MapLibre, fill-extrusion), выбор дома, подсветка.
- Карточка-вердикт: 3 главных минуса с расстояниями, 3 плюса, перцентиль
  по району, «Что проверить лично», строка «Из чего: 5.0 + … = …».
- Поиск по адресу (2 511 зданий с адресом), URL `?building=<id>`.
- Минимальная подложка: крупные улицы из `redzones` нейтральным слоем.
- Тёмная тема, маршруты Valhalla, год постройки (1 715 / 3 306 зданий).
- CI: Python-тесты, `tsc`, `next build`.
- Онлайн-деплой: автоматически после merge в `main`, данные из ветки
  `data`; вручную — только откат.

Ключевые коммиты:

- `08a1a40` — merge PR #7, deploy из ветки `data`.
- `65b7fee` — backup script/runbook.
- `63236ab` — agent grid, CI, прототип `/r/<id>`.

Ветки:

- `main` — код, защищена.
- `data` — только `data.zip` со снимком данных.
- `gh-pages` — опубликованный сайт, защищена от force/delete.
- `lane/web`, `lane/pipeline` — worktrees для агентов.

## 3. Архитектура

```text
OSM / data.mos.ru / ГИС ЖКХ
        ↓
PostGIS (pipeline/*.py)
        ↓
scoring.py + config/scoring.json
        ↓
web/public/data/*.geojson + routes/*.json
        ↓
data.zip → ветка data
        ↓
GitHub Actions Deploy (manual) → gh-pages
```

- Фронт: Next.js 16, TypeScript strict, Tailwind, MapLibre. Статический
  экспорт, `basePath: /karta-okruzheniya`, `distDir: dist`.
- Пайплайн: Python + PostGIS + osmium; Valhalla — предрасчёт маршрутов.
- Данные в `main` не хранятся: `web/public/data` в `.gitignore`.
- Важное: `data.zip` в ветке `data` собран **Python zipfile** с
  forward-slash путями. `Compress-Archive` на Windows кладёт backslash,
  и `unzip` на Linux падает. Не использовать его для этого архива.

## 4. Безопасность и процесс

Сделано:

- `main`: только PR, обязательные checks `Python tests` и
  `TypeScript + Next build`, 0 approvals (соло), запрет force/delete,
  правила применяются к админу.
- `gh-pages`: запрет force/delete; обычный deploy-пуш разрешён.
- Environment `production` с обязательным approve снят 19.11 по решению
  владельца: деплой идёт сам после merge. Страховка — обязательный CI и
  `pipeline/validate_export.py` перед сборкой.
- Actions: pinned by SHA, fork PR требуют approval, default token read-only.
- Dependabot: graph, alerts, security updates, grouped updates — on.
- Secret protection: scanning on, push protection on.
- `docs/SECURITY.md`, `.opencode/opencode.json`, Dependabot config.
- 2FA — **не включена**, владелец сделает сам.

Правила:

1. Прямой push в `main` невозможен. Ветка → PR → CI → merge.
2. Агент коммитит и пушит только свою рабочую ветку; merge в `main` и
   деплой — человек.
3. Один файл — один владелец. Web не трогает `pipeline/`, pipeline не
   трогает `web/`.
4. Pipeline — одна полоса: PostGIS/Valhalla не любят параллельных импортов.
5. Любое изменение данных/формулы — таблица «до/после» по 10 домам.
6. Не вставлять токены/пароли в чат. Не вытаскивать их из credential
   manager.

Worktrees:

- `C:\Users\starg\karta-web` — агент `web`
- `C:\Users\starg\karta-pipeline` — агент `pipeline`

Они созданы от старого коммита; перед работой обновить:
`git fetch && git reset --hard origin/main` (или пересоздать).

## 5. Команды

Тесты:

```powershell
pipeline\.venv\Scripts\python.exe -m unittest discover -s pipeline -p "test_*.py" -v
```

Typecheck/сборка локально:

```powershell
C:\kimi-tools\node.exe web\node_modules\typescript\bin\tsc --noEmit -p web\tsconfig.json
```

Локальный preview (Pages-раскладка):

```powershell
# 1) собрать на ASCII-пути (Turbopack падает на кириллице)
#    скопировать web/ в C:\kimi-build, затем:
cd C:\kimi-build
C:\kimi-tools\node.exe node_modules\next\dist\bin\next build
# 2) разложить dist в .pages-test\karta-okruzheniya
# 3) запустить:
C:\kimi-tools\node.exe scripts\serve-test.mjs .pages-test 3014
# http://localhost:3014/karta-okruzheniya/?building=<id>
```

`serve-test.mjs` обязан отдавать `Content-Length`: без него Chrome за
прокси зависает на module worker MapLibre.

Данные:

```powershell
# zip только через Python zipfile, не Compress-Archive
python -c "import zipfile,pathlib; root=pathlib.Path('web/public/data'); z=zipfile.ZipFile('data.zip','w',zipfile.ZIP_DEFLATED); [z.write(p,p.relative_to(root).as_posix()) for p in root.rglob('*') if p.is_file()]; z.close()"
```

Деплой:

1. Merge PR в `main` — дальше автоматически: CI → Deploy → `gh-pages`.
2. Откат данных: обновить `data.zip` в ветке `data` (см.
   `docs/DATA_RELEASE.md`) и запустить Actions → **Deploy** с `data_ref=data`.
3. Откат кода: revert-PR обычным способом — деплой уедет сам.

Бэкап:

```powershell
.\scripts\backup.ps1 -Target E:\karta-backup
```

Без Docker — `-SkipDocker` (тогда нет дампа PostGIS). Локальный снимок
уже есть: `C:\Users\starg\karta-backup\karta-backup-2026-11-19_1212`.

## 6. Где что лежит

| Файл | Что |
|---|---|
| `web/app/map.tsx` | карта, выбор, поиск, стили, URL |
| `web/app/building-sheet.tsx` | карточка-вердикт, пороги минусов |
| `web/app/search-box.tsx` | поиск по адресу |
| `web/app/layers-panel.tsx` | слои, тема |
| `pipeline/scoring.py` | формула, бонусы/штрафы |
| `config/scoring.json` | все числа формулы |
| `pipeline/export_map_data.py` | geojson + routes для фронта |
| `pipeline/isochrones_valhalla.py` | предрасчёт изохрон 5/10/15 по домам |
| `web/scripts/generate-building-pages.mjs` | прототип `/r/<id>` |
| `.github/workflows/ci.yml` | тесты + сборка |
| `.github/workflows/deploy.yml` | автодеплой после merge (ручной — откат) |
| `.github/workflows/autopilot.yml` | тесты → PR → merge → deploy по push ветки |
| `docs/DATA_RELEASE.md` | как обновлять снимок данных |
| `docs/SECURITY.md` | модель угроз |
| `docs/AGENT_GRID.md` | роли агентов и правила |

## 7. Что болит (не забыть)

1. **Эталон из Gemini — не эталон.** 21/30 по полосам, но константа 6.0
   даёт 26/30. Формулу не крутить; доказана только групповая сортировка
   (хорошие 6.52 / обычные 5.72 / с минусами 5.37). Нужна автоматическая
   валидация фактов, а не ручные 10 домов.
2. ~~**`rail_platform` (МЦД)**~~ — ИСПРАВЛЕНО 19.11: платформы выгружаются
   в POI (28 в Пресне) и рисуются в слое «Метро и электрички» отдельной
   иконкой с подписью «Электричка · N мин».
3. **Год постройки**: 1 715 из 3 306, а не 1 755. В UI нет источника.
4. **Нет полной подложки/названий улиц**; только крупные улицы.
5. **Масштаб**: 4 МБ geojson, 3 306 route-файлов, 27 МБ dist. 30 районов
   потребуют PMTiles + route-pack + data pipeline.
6. **Valhalla-тайлы**: в docs 3.5 ГБ, по факту `data/processed/valhalla`
   ~0.9 ГБ. Возможно, часть потеряна; проверить перед пересчётом.
7. **Юридика**: OSM ODbL (атрибуция), data.mos.ru (условия набора),
   ГИС ЖКХ (нет открытой лицензии, массовый сбор). Не строить платное
   на годе постройки до разрешения.
8. **Статический `/r/<id>`** не входит в CI-сборку — сейчас 404.
   Массовую генерацию не включать, пока карточка не проверена.
9. **Средний Android** не протестирован; был только флагман.
10. ~~**PostGIS dump** в бэкап не попал~~ — ИСПРАВЛЕНО 19.11: скрипт
    ложно падал на `docker compose up` (stderr + `$ErrorActionPreference=Stop`
    в PS 5.1) и молча пропускал дамп. Бэкап 15:12 содержит `okrug.dump`
    589 МБ, проверен `pg_restore --list` (66 объектов).

## 8. Следующая задача (рекомендация)

**Автоматическая валидация данных + confidence.** Не ручные 10 домов.

Шаги:

1. `pipeline/validate_export.py`:
   - читает `web/public/data/presnya_buildings.geojson` и `config/scoring.json`;
   - проверяет: id уникальны, index 0–10, breakdown сходится с индексом
     (с учётом clamp), нет отрицательных расстояний/минут, нет дублей POI,
     адрес не «16» без улицы, год не 0/1700/2050;
   - считает покрытие: адрес, год, высота, POI по типам;
   - возвращает exit code 1 при критичных ошибках.
2. Добавить проверку в data-пайплайн (позже — в `data.yml`).
3. Confidence по источникам: `high` (2+ источника согласны), `medium`
   (один авторитетный), `low` (только OSM). В карточке low-факты
   помечать «проверить», а не показывать как истину.
4. Golden set 10–20 стабильных домов — автотест, не ручной review.

Критерий: `validate_export.py` на текущих данных даёт отчёт и не падает
на ожидаемых clamp-случаях; ошибки перечислены с id и адресом.

## 9. Чего не делать

- Не пушить в `main` напрямую.
- Не генерировать 3 306 OG-страниц до готовности карточки/данных.
- Не добавлять PM2.5/заводы без измерений.
- Не масштабировать на 30 районов до PMTiles.
- Не менять `config/scoring.json` без тестов и записи в decisions.
- Не давать агентам push в `main`/`gh-pages`, force-push и `npm run deploy`.
- Не вставлять секреты в чат/репозиторий.
- Не делать новый HUD-неон в продукте (только маркетинг).

## 10. Открытые вопросы владельцу

- Есть ли разрешение на ГИС ЖКХ/data.mos.ru для коммерческого продукта?
- Кто целевой пользователь: арендатор или покупатель? Сейчас B2C-жилец.
- Когда включаем 2FA и переносим бэкап на внешний диск?
- Нужен ли один прототип `/r/<id>` в CI-сборке или ждём готовности карточки?
