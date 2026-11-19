# Бэкап данных и БД

GitHub хранит код и историю, но **не** хранит:

- `data/raw` — OSM PBF, реестр, выгрузки (~1 ГБ);
- `data/processed` — Valhalla-тайлы, кэши, json (~1 ГБ);
- Docker-volume PostGIS.

Без них проект не пересобрать с нуля.

## Куда

Только **внешний диск** или сетевое хранилище. Бэкап на том же `C:` — это
защита от случайного удаления, но не от смерти диска.

## Как

Из корня репозитория:

```powershell
.\scripts\backup.ps1 -Target D:\karta-backup
```

Скрипт создаёт `karta-backup-YYYY-MM-DD_HHmm`:

```text
repo.bundle          — вся git-история (все ветки)
scoring.json
reference_set.md
SECURITY.md
data/raw/
data/processed/      — включая Valhalla-тайлы
okrug.dump           — дамп PostGIS (если Docker запущен)
manifest.txt         — дата, commit, размер
```

Если Docker не запущен, дамп пропускается с предупреждением; остальное
копируется. Для дампа:

```powershell
# Docker Desktop должен быть запущен
docker compose up -d postgis
.\scripts\backup.ps1 -Target D:\karta-backup
```

## Восстановление

1. Скопировать `data/raw` и `data/processed` обратно в репозиторий.
2. Поднять PostGIS: `docker compose up -d`.
3. Восстановить дамп:

```powershell
docker cp okrug.dump okrug-postgis:/tmp/okrug.dump
docker exec -it okrug-postgis pg_restore -U okrug -d okrug --clean --if-exists /tmp/okrug.dump
```

4. Проверить: `docker exec okrug-postgis psql -U okrug -d okrug -c "select count(*) from scores;"`.

## Расписание

Минимум — раз в неделю и перед каждым обновлением данных. Лучше — перед
любым экспериментом агента с `data/` или БД.
