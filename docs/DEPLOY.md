# Деплой на GitHub Pages

Карта — статический экспорт Next.js (`output: "export"` в `web/next.config.ts`),
сайт живёт в подпапке репозитория: `basePath: "/karta-okruzheniya"`.
Все ресурсы в коде — относительные пути (fetch GeoJSON, воркер MapLibre).

Публичный URL: **https://montecristo1483.github.io/karta-okruzheniya/**

Ссылка на конкретный дом выглядит так:
`https://montecristo1483.github.io/karta-okruzheniya/?building=<osm_id>`
(кнопка «Отправить клиенту» в шторке дома формирует и копирует её сама).

## Способ 1 — скрипт (рекомендуется)

```bash
cd web
npm install        # один раз, ставит gh-pages из devDependencies
npm run deploy     # next build + пуш dist/ в ветку gh-pages (.nojekyll добавляется сам)
```

ОГРАНИЧЕНИЕ: при тысячах файлов в dist (например data/routes/*.json)
`gh-pages` падает с `spawn ENAMETOOLONG` — готовит один гигантский
`git rm`, длиннее лимита командной строки Windows. Тогда — Способ 1б.

## Способ 1б — git worktree вручную (деплой с большим числом файлов)

```bash
cd web && npm run build        # собрать
cd ..                          # корень репозитория
git fetch origin gh-pages
git worktree add .deploy-tmp gh-pages   # если ветки нет локально:
# git worktree add --track -b gh-pages .deploy-tmp origin/gh-pages
cd .deploy-tmp
git rm -rf --quiet .
cp -r ../web/dist/. .
touch .nojekyll                # без него Pages гоняет Jekyll и 404 на _next/
git add -A && git commit -m "deploy" && git push origin gh-pages
cd .. && git worktree remove --force .deploy-tmp
```

## Способ 2 — вручную через настройки репозитория

1. `cd web && npm run build` — собрать, результат в `web/dist/`.
2. Выложить содержимое `dist/` в ветку `gh-pages` (корень ветки).
3. GitHub → Settings → Pages → Source: «Deploy from a branch» →
   ветка `gh-pages`, папка `/ (root)` → Save.
4. Через минуту-две сайт доступен по URL выше.

## Проверка локально до деплоя

`npx serve` под Windows врёт (404 на существующие файлы, 500 на чанки) —
проверяем раскладку Pages (сайт в подпапке) кастомным сервером:

```bash
cd web
npm run build
mkdir .pages-test && cp -r dist .pages-test/karta-okruzheniya
node scripts/serve-test.mjs .pages-test 3000
# открыть http://localhost:3000/karta-okruzheniya/?building=<osm_id>
```

dev-режим (`npm run dev`) тоже работает — страница теперь на
http://localhost:3000/karta-okruzheniya/ (basePath действует и в dev).

## Важно

- После деплоя проверь PWA в Chrome DevTools → Lighthouse → PWA (или вкладка
  Application → Manifest: имя, иконки, цвета; Application → Service Workers:
  sw.js активен в scope /karta-okruzheniya/).
- `web/dist/` не коммитится (в `.gitignore`) — деплоится только в ветку `gh-pages`.
- После смены `basePath` (переименование репозитория) править:
  `next.config.ts`, `PUBLIC_BASE` в `app/building-sheet.tsx`, этот файл.
- Данные карты (`public/data/*.geojson`) копируются в `dist/` автоматически.
