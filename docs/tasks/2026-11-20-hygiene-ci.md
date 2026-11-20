# Задача D1: guard от двойного мержа и кэш образа tippecanoe

Дата: 2026-11-20. Гигиена CI. Только workflows, код приложения не трогаем.

## Прочитать
1. `docs/DEBT.md` — пункты 1 и 2
2. `docs/STATE.md`
3. `.github/workflows/autopilot.yml` (весь, 64 строки)
4. `.github/workflows/deploy.yml` — шаг `Build tippecanoe image`

Не читай `web/app/*`, `*.geojson`, `dist/`, `node_modules/`.

## Задача 1: не мержить пустой дифф
Автопилот трижды смержил одну ветку дважды (#60/#61, #70/#71, #77/#78):
второй push приходил после первого мержа, ветка пересоздавалась с тем же
содержимым, и появлялся пустой squash-коммит.

В job `publish`, **перед** шагом `Open PR if missing`:
```yaml
- name: Skip if already merged
  run: |
    set -euo pipefail
    git fetch origin main
    if git diff --quiet origin/main...HEAD; then
      echo "Ветка не содержит изменений относительно main — выходим."
      echo "skip=1" >> "$GITHUB_OUTPUT"
    fi
```
Дальше шаги `Open PR if missing`, `Merge and deploy` пропускаются при
`skip` (через `if: steps.<id>.outputs.skip != '1'`).
Если проще — можно сделать один шаг с проверкой в начале: пустой дифф →
`exit 0` для всего job. Главное: **пустой дифф не должен приводить к merge**.

## Задача 2: кэш образа tippecanoe
Сейчас Deploy собирает образ 2 минуты на каждом прогоне (3 минуты деплоя
из 10). Добавить кэш:
```yaml
- uses: actions/cache@v4
  id: tippecanoe-cache
  with:
    path: /tmp/tippecanoe-image.tar
    key: tippecanoe-2.79.0-${{ hashFiles('pipeline/docker/tippecanoe.Dockerfile') }}

- name: Load tippecanoe image
  if: steps.tippecanoe-cache.outputs.cache-hit == 'true'
  run: docker load -i /tmp/tippecanoe-image.tar

- name: Build tippecanoe image
  if: steps.tippecanoe-cache.outputs.cache-hit != 'true'
  run: |
    docker build -f pipeline/docker/tippecanoe.Dockerfile -t karta-tippecanoe:2.79.0 .
    docker save karta-tippecanoe:2.79.0 -o /tmp/tippecanoe-image.tar
```
Проверить, что шаг `Build tiles` по-прежнему видит образ и деплой зелёный.

## Что нельзя
- Менять `web/`, данные, `scoring.json`, `sw.js`.
- Ослаблять тестовый гейт: тесты остаются обязательными.
- main/gh-pages, force-push, `npm run deploy`.

## Ветка
```powershell
cd C:\Users\starg\karta-web
git fetch origin
git switch -c task/hygiene-ci origin/main
```

## Отчёт
1. Что изменено в двух workflow.
2. Deploy: время до/после (по логу run), кэш сработал или нет.
3. Доказательство, что пустой дифф не мержится (лог шага или dry-run).
4. Риски. 5. PR.
Не более 30 минут.
