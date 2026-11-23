# Data-снимки и онлайн-деплой

Данные (`web/public/data`) в `main` не хранятся. Основной канал доставки —
**GitHub Release с тегом `data`** и ассетом `data.zip`; ветка **`data`**
осталась запасным путём и историей для отката. Полный прогон даёт архив
~380 МБ, а git-блоб ограничен 100 МБ, поэтому Release — единственный
рабочий способ довезти такой архив.

Deploy берёт `data.zip` в таком порядке:

1. ассет `data.zip` из Release с тегом `data_tag` (по умолчанию `data`);
2. если релиза с таким тегом нет — ветка `data_ref` (по умолчанию `data`),
   как раньше;
3. если нет ни того, ни другого — деплой падает с понятной ошибкой.

## 1. Собрать архив (на машине, где есть актуальные данные)

Собирать **Python zipfile** (не `Compress-Archive`): содержимое каталога
лежит в корне архива, без верхней папки.

```powershell
pipeline\.venv\Scripts\python.exe -c "import zipfile,pathlib; root=pathlib.Path('web/public/data'); z=zipfile.ZipFile('data.zip','w',zipfile.ZIP_DEFLATED); [z.write(p, p.relative_to(root).as_posix()) for p in root.rglob('*') if p.is_file()]; z.close()"
```

Внутри `data.zip` (формат `--format new`, три района):

```text
districts/manifest.json
districts/<id>/buildings.geojson
districts/<id>/poi.geojson
districts/<id>/basemap.geojson
districts/<id>/redzones.geojson
routes/
isochrones/
```

Старый формат (один район) тоже поддерживается: `presnya_buildings.geojson`,
`presnya_poi.geojson`, `presnya_redzones.geojson`, `basemap.geojson` в корне.

`basemap.geojson` собирается из локального OSM-дампа:

```powershell
pipeline\.venv\Scripts\python.exe pipeline\build_basemap.py `
  --pbf <ASCII-путь>\moscow.osm.pbf `
  --out web\public\data\basemap.geojson
```

`data.zip` в `.gitignore` — в `main` не коммитим.

## 2. Опубликовать Release с тегом `data`

Ручная загрузка ассета — ожидаемая, один раз на выпуск данных: локального
`gh` нет, автоматическую загрузку не изобретаем.

**Первый выпуск.** GitHub → Releases → **Draft a new release** →
Choose a tag → создать тег `data` → заголовок `data` → прикрепить
`data.zip` → **Publish release**.

**Обновление (новый снимок).** Открыть существующий релиз с тегом `data`,
удалить старый ассет `data.zip`, загрузить новый, сохранить. Тег не
пересоздавать: Deploy ищет релиз по имени тега.

Проверка после загрузки (локально, если `gh` есть; иначе — глазами в UI,
размер и sha256 в описании релиза):

```powershell
gh release download data --pattern data.zip -O data.zip --clobber
Get-FileHash data.zip -Algorithm SHA256
gh release view data --json assets
```

`sha256` и размер пишем в описание релиза — по ним сверяем, что на проде
тот самый снимок.

## 3. Запасной путь: ветка `data`

Используется, только если релиза нет (или для отката на старый снимок из
истории ветки). Ветка `data` — отдельная, без кода:

```powershell
git fetch origin data
git worktree add --detach C:\Users\starg\karta-data-branch
cd C:\Users\starg\karta-data-branch
git switch data
git rm -rf .
Copy-Item -Force <путь-к-репозиторию>\data.zip .\data.zip
git add data.zip
git commit -m "data snapshot YYYY-MM-DD"
git push origin data
cd <путь-к-репозиторию>
git worktree remove --force C:\Users\starg\karta-data-branch
```

Если ветки `data` ещё нет:

```powershell
git checkout --orphan data
git rm -rf .
```

## 4. Запустить деплой

Обычно деплой запускается сам после merge в `main` (autopilot → Deploy).
Ручной запуск: GitHub → Actions → **Deploy** → Run workflow.

Входы workflow:

- `data_tag` — тег релиза с `data.zip` (по умолчанию `data`);
- `data_ref` — ветка-запасной путь (по умолчанию `data`).

## 5. Чек-лист релиза (делать по шагам, не по памяти)

1. Собрать выгрузку в отдельный каталог (не в рабочий `web/public/data`).
2. При необходимости долить поля: `pipeline/add_breakdown_to_routes.py --data-dir <каталог>`
   (идемпотентно, без PostGIS).
3. Проверить: `pipeline/validate_export.py <каталог>` → **0 критичных**;
   `pipeline/check_golden.py <каталог>` → 18/18 (если есть данные golden).
4. Сверить, что `presnya_buildings.geojson` не изменился, если задача его
   не касалась (sha256 до/после).
5. Упаковать **Python zipfile** (не Compress-Archive): содержимое каталога
   в корне архива, без верхней папки.
6. Загрузить `data.zip` в Release с тегом `data` (раздел 2), записать
   размер и sha256 в описание релиза.
7. Записать в `docs/decisions.md`: что изменилось, размер архива, число
   файлов, результат валидатора.
8. Деплой: push в `main` (docs-коммит) или ручной Deploy с `data_tag`/`data_ref`.
9. Проверить на проде один живой файл, который должен был измениться
   (например `data/routes/<id>.json` — наличие нового поля).
10. При сбое — откат (раздел 6).

## 6. Откат

- **Данные:** вернуть в релиз с тегом `data` предыдущий `data.zip`
  (из истории ассетов/локальной копии) и запустить Deploy. Пока релиз
  не обновлён, Deploy продолжает брать текущий тег.
- **Совсем без релиза:** удалить ассет (или сам релиз) — Deploy уйдёт на
  ветку `data_ref`, где история снимков хранится в коммитах.
  `git log origin/data -- data.zip` покажет предыдущие снимки.
- **Код:** как раньше — revert в `main`; данные при этом не трогаются.

## 7. Защита

- Deploy запускается по push в `main` и вручную; перед сборкой сайта данные
  проверяют `pipeline/validate_export.py` и `pipeline/check_golden.py`
  (golden фильтруется по `is_home`; набор — 18 фактов).
- Бот (`autopilot.yml`) мержит PR только после зелёных тестов.
- Ветка `gh-pages` защищена от force push и удаления; агенту push туда запрещён.
- `GITHUB_TOKEN` живёт только внутри Actions; на ноутбук не попадает.

## 8. Что дальше

- `data.yml` будет собирать `data.zip` автоматически (PostGIS + OSM +
  data.mos.ru) и обновлять ветку `data`; автозагрузку в Release включим,
  когда появится токен с правом `contents: write` (или `gh` на машине).
- Valhalla — отдельной фазой: тяжёлые тайлы, кэш, только изменённые дома.
