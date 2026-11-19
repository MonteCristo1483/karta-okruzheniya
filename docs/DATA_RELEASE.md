# Data-релизы: как обновлять данные без ноутбука

Данные (`web/public/data`) в git не хранятся: 20 МБ geojson + маршруты.
Поэтому они живут отдельным артефактом — GitHub Release. Код и данные
версионируются раздельно, сайт всегда знает, на какой снимок смотрит.

## 1. Собрать архив (на машине, где есть актуальные данные)

PowerShell из корня репозитория:

```powershell
Compress-Archive -Path web/public/data/* -DestinationPath data.zip -Force
```

Внутри архива должны лежать:

```text
presnya_buildings.geojson
presnya_poi.geojson
presnya_redzones.geojson
routes/
```

`data-*.zip` в `.gitignore` — в репозиторий не коммитим.

## 2. Опубликовать релиз

1. Открой https://github.com/MonteCristo1483/karta-okruzheniya/releases/new
2. Tag: `data-2026-11-19`
3. Title: `Data snapshot 2026-11-19`
4. Прикрепи `data.zip` (имя файла должно быть ровно `data.zip`).
5. Publish release.

Проверка: в релизе должен быть asset `data.zip`.

## 3. Запустить деплой

GitHub → Actions → **Deploy (manual)** → Run workflow:

- `data_tag`: `data-2026-11-19`

Workflow скачает `data.zip`, соберёт сайт на Linux и опубликует `gh-pages`.
Локальный `C:\kimi-build`, кириллица и `ENAMETOOLONG` больше не участвуют.

## 4. Защита

- Deploy запускается **только вручную** (`workflow_dispatch`), не на push.
- Environment `production` можно защитить правилом «Required reviewers»:
  Settings → Environments → production. Тогда деплой ждёт подтверждения.
- Токен `GITHUB_TOKEN` живёт только внутри Actions; на ноутбук не попадает.
- Ветку `gh-pages` лучше защитить от прямых push, оставив публикацию CI.

## 5. Что дальше

- `data.yml` будет собирать этот архив автоматически (PostGIS + OSM +
  data.mos.ru), затем создавать релиз.
- Valhalla — отдельной фазой: тяжёлые тайлы, кэш, только изменённые дома.
