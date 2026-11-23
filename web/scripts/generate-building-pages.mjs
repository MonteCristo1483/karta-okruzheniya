#!/usr/bin/env node
/**
 * Статические страницы домов для шаринга и SEO.
 *
 * Зачем: GitHub Pages не умеет серверный OG, поэтому на каждый дом
 * генерируется /r/<id>/index.html с title/description/og:image.
 * Страница — лёгкая витрина, кнопка ведёт в интерактивную карту.
 *
 * Использование:
 *   node scripts/generate-building-pages.mjs --id 28121119   # одна страница
 *   node scripts/generate-building-pages.mjs                 # все дома + sitemap
 *
 * Читает public/data/districts/<id>/buildings.geojson (или старый export),
 * пишет dist/r/<id>/index.html.
 * Данные в git не коммитятся — скрипт запускается после пайплайна/сборки.
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const PUBLIC_DATA = join(WEB_ROOT, "public", "data");
const LEGACY_DATA_PATH = join(PUBLIC_DATA, "presnya_buildings.geojson");
const MANIFEST_PATH = join(PUBLIC_DATA, "districts", "manifest.json");
const DIST_ROOT = join(WEB_ROOT, "dist");
const BASE = "https://montecristo1483.github.io/karta-okruzheniya";
const OG_IMAGE = `${BASE}/og.jpg`;
const CONFIG_PATH = fileURLToPath(new URL("../../config/scoring.json", import.meta.url));
const DATA_DATE = new Date().toISOString().slice(0, 10);
let FORMULA_VERSION = "?";
try {
  FORMULA_VERSION = JSON.parse(readFileSync(CONFIG_PATH, "utf8")).meta?.version ?? "?";
} catch {
  // конфиг вне web/ может отсутствовать в изолированной сборке
}

const BONUS_LABELS = {
  transport: "Транспорт",
  schools: "Школы",
  parks: "Парки",
  kindergartens: "Детские сады",
  shops: "Магазины",
};

const PENALTY_LABELS = {
  road: "Магистраль",
  railway: "Ж/д",
  construction: "Стройка",
  industrial: "Промзона",
};

const PERCENTILE_LABELS = {
  road: "близости к магистрали",
  railway: "близости к ж/д",
  construction: "близости к стройке",
  industrial: "близости к промзоне",
};

const PENALTY_KINDS = ["road", "railway", "construction", "industrial"];
const RED_FLAG_PENALTY = -0.2;
const SEVERE_PENALTY = -0.5;

function loadDatasets() {
  if (existsSync(MANIFEST_PATH)) {
    const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
    return manifest.districts.map((district) => {
      const path = join(PUBLIC_DATA, "districts", String(district.id), "buildings.geojson");
      if (!existsSync(path)) throw new Error(`Нет данных района: ${path}`);
      const data = JSON.parse(readFileSync(path, "utf8"));
      const features = (data.features ?? []).filter(
        (feature) => feature.properties?.is_home === true,
      );
      const name = String(district.name ?? "");
      const phrase = /Арбат/i.test(name)
        ? "Арбате"
        : /Тверск/i.test(name)
          ? "Тверском районе"
          : "Пресне";
      return { phrase, features };
    });
  }

  if (!existsSync(LEGACY_DATA_PATH)) {
    throw new Error(`Нет данных домов: ${LEGACY_DATA_PATH}`);
  }
  const data = JSON.parse(readFileSync(LEGACY_DATA_PATH, "utf8"));
  return [{ phrase: "Пресне", features: data.features ?? [] }];
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#39;";
    }
  });
}

function label(kind) {
  return PENALTY_LABELS[kind] ?? kind;
}

function distancePhrase(distance) {
  return distance < 15 ? "рядом" : `в ${Math.round(distance)} м`;
}

function signed(value, digits = 1) {
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
}

function indexColor(index) {
  if (index === null) return "#c9ced3";
  if (index < 5) return "#d98f86";
  if (index < 7) return "#c9bf9e";
  return "#7fb069";
}

function buildFacts(props) {
  const b = props.breakdown;
  if (!b) {
    return {
      index: null,
      red: [],
      green: [],
      soft: [],
      checks: [],
      verdict: "Недостаточно данных — проверьте окружение лично",
      math: "",
    };
  }

  const penalties = Object.entries(b.penalties ?? {})
    .filter(([, entry]) => entry !== null)
    .sort((a, b2) => a[1].penalty - b2[1].penalty);
  const bonuses = Object.entries(b.bonuses ?? {})
    .filter(([, entry]) => entry !== null)
    .sort((a, b2) => b2[1].bonus - a[1].bonus);

  const red = penalties
    .filter(([, entry]) => entry.penalty < RED_FLAG_PENALTY)
    .slice(0, 3);
  const green = bonuses.filter(([, entry]) => entry.bonus > 0.05).slice(0, 3);
  const soft = bonuses
    .filter(([, entry]) => entry.bonus < 0.05 && entry.minutes > 15)
    .slice(0, 2)
    .map(([kind, entry]) => `${BONUS_LABELS[kind] ?? kind} — ${Math.round(entry.minutes)} мин`);

  const severe = red.filter(([, entry]) => entry.penalty < SEVERE_PENALTY);
  let verdict;
  if (severe.length >= 2) {
    const [a, b2] = severe;
    verdict = `Шумное место: ${label(a[0]).toLowerCase()} ${distancePhrase(a[1].distance_m)} и ${label(b2[0]).toLowerCase()} ${distancePhrase(b2[1].distance_m)}`;
  } else if (severe.length === 1) {
    const [kind, entry] = severe[0];
    verdict = `Главный минус: ${label(kind).toLowerCase()} ${distancePhrase(entry.distance_m)}`;
  } else if (green.length >= 3) {
    verdict = "Спокойный дом с хорошей инфраструктурой";
  } else if (green.length >= 1) {
    verdict = "Есть плюсы, но проверьте детали";
  } else {
    verdict = "Мало данных — проверьте окружение лично";
  }

  const checks = [];
  for (const [kind, entry] of red) {
    if (kind === "road" && entry.distance_m < 200) {
      checks.push("Выйдите на балкон в час пик: слышно ли магистраль?");
    } else if (kind === "railway" && entry.distance_m < 300) {
      checks.push("Послушайте поезда вечером — ж/д рядом.");
    } else if (kind === "construction") {
      checks.push("Уточните, что строят и до какого года.");
    } else if (kind === "industrial") {
      checks.push("Проверьте, работает ли производство и есть ли выбросы.");
    }
  }
  const byKey = Object.fromEntries(bonuses);
  if (byKey.transport && byKey.transport.bonus < 0.05) {
    checks.push("Пройдите маршрут до метро в час пик: в карточке больше 15 минут.");
  }
  if (byKey.schools && byKey.schools.bonus < 0.05) {
    checks.push("Проверьте, как ребёнок дойдёт до школы.");
  }
  if (checks.length === 0) checks.push("Посмотрите двор вечером и парковку.");

  const base = b.base ?? 5;
  const bonusSum = bonuses.reduce((sum, [, entry]) => sum + entry.bonus, 0);
  const penaltySum = penalties.reduce((sum, [, entry]) => sum + entry.penalty, 0);
  const rawTotal = base + bonusSum + penaltySum;
  const index = props.index ?? null;
  const clamped = index !== null && Math.abs(rawTotal - index) > 0.05;
  const math = `Из чего: ${base.toFixed(1)} + ${bonusSum.toFixed(1)} − ${Math.abs(penaltySum).toFixed(1)} = ${rawTotal.toFixed(1)}${clamped ? ` → ${index.toFixed(1)}` : ""}`;

  return {
    index,
    red,
    green,
    soft,
    checks: checks.slice(0, 3),
    verdict,
    math,
    base,
    bonusSum,
    penaltySum,
    rawTotal,
    clamped,
  };
}

function buildDescription(props, facts) {
  const red = facts.red
    .slice(0, 2)
    .map(([kind, entry]) => `${label(kind).toLowerCase()} ${distancePhrase(entry.distance_m)}`)
    .join(", ");
  const green = facts.green
    .slice(0, 2)
    .map(([kind, entry]) => `${BONUS_LABELS[kind] ?? kind} ${Math.round(entry.minutes)} мин`)
    .join(", ");
  const parts = [`Индекс окружения ${facts.index === null ? "—" : facts.index.toFixed(1)}/10.`];
  if (red) parts.push(`Минусы: ${red}.`);
  if (green) parts.push(`Плюсы: ${green}.`);
  parts.push("Справочная информация по открытым данным.");
  const text = parts.join(" ");
  return text.length > 300 ? `${text.slice(0, 297)}...` : text;
}

function pageHtml(props, facts, percentileLine) {
  const address = props.address || `Дом ${props.id}`;
  const title = `${address} — индекс окружения ${facts.index === null ? "—" : facts.index.toFixed(1)}/10`;
  const description = buildDescription(props, facts);
  const canonical = `${BASE}/r/${props.id}/`;
  const pct = facts.index === null ? 0 : Math.max(0, Math.min(100, facts.index * 10));
  const color = indexColor(facts.index);

  const redRows = facts.red
    .map(
      ([kind, entry]) =>
        `<li><span>${esc(label(kind))}</span><b>${esc(distancePhrase(entry.distance_m))}</b></li>`,
    )
    .join("");
  const greenPills = facts.green
    .map(
      ([kind, entry]) =>
        `<span class="pill">${esc(BONUS_LABELS[kind] ?? kind)} · ${Math.round(entry.minutes)} мин</span>`,
    )
    .join("");
  const checks = facts.checks.map((item) => `<li>${esc(item)}</li>`).join("");
  const soft = facts.soft.length
    ? `<div class="soft">Далеко: ${esc(facts.soft.join(" · "))}</div>`
    : "";

  return `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="${esc(canonical)}">
<link rel="icon" href="${BASE}/icon.svg">
<meta name="theme-color" content="#6a9d76">
<meta property="og:locale" content="ru_RU">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Карта окружения">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${esc(canonical)}">
<meta property="og:image" content="${OG_IMAGE}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Карточка дома: индекс окружения и главные минусы">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title)}">
<meta name="twitter:description" content="${esc(description)}">
<meta name="twitter:image" content="${OG_IMAGE}">
<style>
:root{--ink:#1f2937;--muted:#6b7280;--green:#4f9d64;--amber:#b45309}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:linear-gradient(180deg,#f7f2e8 0%,#e9eff5 55%,#d9e6f0 100%);color:var(--ink)}
.wrap{max-width:640px;margin:0 auto;padding:20px 16px 44px}
.brand{display:flex;align-items:center;gap:10px;margin:0 0 16px 4px;font-weight:700;color:#4b5563}
.logo{width:26px;height:26px;border:6px solid #6a9d76;border-radius:50%;display:inline-block}
.card{background:rgba(255,255,255,.95);border-radius:24px;padding:22px;box-shadow:0 12px 34px rgba(31,41,55,.10)}
h1{font-size:24px;line-height:1.2;margin:0 0 4px}
.year{color:var(--muted);font-size:14px;margin-bottom:18px}
.score{display:flex;align-items:center;gap:16px;margin:0 0 18px}
.ring{width:92px;height:92px;border-radius:50%;flex:0 0 auto;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;position:relative;background:conic-gradient(${color} calc(${pct} * 1%),#e5e7eb 0)}
.ring::before{content:"";position:absolute;inset:9px;border-radius:50%;background:#fff}
.ring b{position:relative;font-size:26px;line-height:1}
.ring small{position:relative;font-size:10px;line-height:1;color:var(--muted)}
.caption{font-size:12px;color:var(--muted)}
.verdict{font-size:15px;font-weight:700;line-height:1.35;margin-top:2px}
.pct{font-size:12px;color:var(--muted);line-height:1.35;margin-top:4px}
.math{font-size:11px;color:#9ca3af;margin-top:4px}
.flags{background:#fef2f2;border-radius:16px;padding:14px 16px;margin:0 0 12px}
.flags h2{font-size:14px;color:#b91c1c;margin:0 0 8px}
.flags ul{list-style:none;margin:0;padding:0}
.flags li{display:flex;justify-content:space-between;gap:12px;font-size:14px;color:#7f1d1d;padding:2px 0}
.flags li b{font-weight:700}
.ok{background:#ecfdf5;color:#065f46;border-radius:16px;padding:14px 16px;font-size:14px;margin:0 0 12px}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.pill{background:#ecfdf5;color:#065f46;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:600}
.soft{color:var(--amber);font-size:12px;margin:0 0 10px}
.checks{background:#f8fafc;border-radius:16px;padding:14px 16px;margin:14px 0}
.checks h2{font-size:14px;margin:0 0 8px}
.checks ul{margin:0;padding-left:18px;color:#475569;font-size:14px;line-height:1.45}
.cta{display:block;text-align:center;background:#10b981;color:#fff;text-decoration:none;font-weight:700;padding:15px;border-radius:16px;margin:18px 0 10px}
.cta:active{background:#059669}
.disclaimer{font-size:11px;color:#9ca3af;text-align:center;line-height:1.4;margin:8px 0}
.sources{font-size:11px;color:#9ca3af;text-align:center;line-height:1.4;margin:0}
@media (max-width:420px){.card{padding:18px;border-radius:20px}h1{font-size:21px}.ring{width:80px;height:80px}.ring b{font-size:22px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><span class="logo"></span><span>Карта окружения</span></div>
  <main class="card">
    <h1>${esc(address)}</h1>
    ${props.year != null ? `<div class="year">Год постройки — ${esc(props.year)}</div>` : ""}
    <div class="score">
      <div class="ring" aria-label="Индекс окружения ${facts.index === null ? "нет данных" : facts.index.toFixed(1) + " из 10"}"><b>${facts.index === null ? "—" : facts.index.toFixed(1)}</b><small>/10</small></div>
      <div>
        <div class="caption">Индекс окружения</div>
        <div class="verdict">${esc(facts.verdict)}</div>
        ${percentileLine ? `<div class="pct">${esc(percentileLine)}</div>` : ""}
        ${facts.math ? `<div class="math">${esc(facts.math)}</div>` : ""}
      </div>
    </div>
    ${
      facts.red.length
        ? `<section class="flags"><h2>⚠ Главные минусы</h2><ul>${redRows}</ul></section>`
        : `<div class="ok">Явных красных флагов рядом нет — но проверьте лично.</div>`
    }
    ${greenPills ? `<div class="pills">${greenPills}</div>` : ""}
    ${soft}
    ${checks ? `<section class="checks"><h2>Что проверить лично</h2><ul>${checks}</ul></section>` : ""}
    <a class="cta" href="${BASE}/?building=${props.id}">Открыть на карте</a>
    <p class="disclaimer">Справочная информация по открытым данным, не является замером или оценкой стоимости.</p>
    <p class="sources">Источники: OpenStreetMap (ODbL), data.mos.ru, ГИС ЖКХ. Данные на ${DATA_DATE}, формула v${FORMULA_VERSION}.</p>
  </main>
</div>
</body>
</html>
`;
}

function main() {
  if (!existsSync(DIST_ROOT)) {
    console.error(`Нет сборки: ${DIST_ROOT}. Сначала next build.`);
    process.exit(1);
  }

  const datasets = loadDatasets().map((dataset) => ({
    ...dataset,
    series: Object.fromEntries(
      PENALTY_KINDS.map((kind) => [
        kind,
        dataset.features.map(
          (feature) =>
            feature.properties.breakdown?.penalties?.[kind]?.distance_m ??
            Number.POSITIVE_INFINITY,
        ),
      ]),
    ),
  }));
  const seenIds = new Set();
  const records = datasets.flatMap((dataset) =>
    dataset.features
      .filter((feature) => {
        const id = String(feature.properties.id);
        if (seenIds.has(id)) return false;
        seenIds.add(id);
        return true;
      })
      .map((feature) => ({ feature, dataset })),
  );

  const percentileFor = (dataset, kind, distance) => {
    const values = dataset.series[kind];
    if (!values?.length) return 0;
    let farther = 0;
    for (const value of values) if (value > distance) farther += 1;
    return farther / values.length;
  };

  const render = (feature, dataset) => {
    const props = feature.properties;
    const facts = buildFacts(props);
    const distanceOf = (kind) =>
      props.breakdown?.penalties?.[kind]?.distance_m ?? Number.POSITIVE_INFINITY;
    const worst = PENALTY_KINDS.map((kind) => ({
      kind,
      p: percentileFor(dataset, kind, distanceOf(kind)),
    }))
      .filter(({ kind, p }) => p >= 0.6 && facts.red.some(([k]) => k === kind))
      .sort((a, b) => b.p - a.p)[0];
    const percentileLine = worst
      ? `По ${PERCENTILE_LABELS[worst.kind]} хуже ${Math.round(worst.p * 100)}% домов в ${dataset.phrase}`
      : "";
    return pageHtml(props, facts, percentileLine);
  };

  const idFlag = process.argv.indexOf("--id");
  const onlyId = idFlag !== -1 ? process.argv[idFlag + 1] : null;

  if (onlyId) {
    const record = records.find(
      ({ feature }) => String(feature.properties.id) === String(onlyId),
    );
    if (!record) {
      console.error(`Дом ${onlyId} не найден в данных районов`);
      process.exit(1);
    }
    const { feature, dataset } = record;
    const dir = join(DIST_ROOT, "r", String(feature.properties.id));
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "index.html"), render(feature, dataset), "utf8");
    console.log(`Готово: dist/r/${feature.properties.id}/index.html`);
    return;
  }

  rmSync(join(DIST_ROOT, "r"), { recursive: true, force: true });
  let count = 0;
  for (const { feature, dataset } of records) {
    const dir = join(DIST_ROOT, "r", String(feature.properties.id));
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "index.html"), render(feature, dataset), "utf8");
    count += 1;
  }

  const urls = records
    .map(({ feature }) => feature)
    .filter((f) => f.properties.address)
    .map(
      (f) =>
        `  <url><loc>${BASE}/r/${f.properties.id}/</loc><changefreq>monthly</changefreq></url>`,
    )
    .join("\n");
  writeFileSync(
    join(DIST_ROOT, "sitemap.xml"),
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`,
    "utf8",
  );
  writeFileSync(
    join(DIST_ROOT, "robots.txt"),
    `User-agent: *\nAllow: /\nSitemap: ${BASE}/sitemap.xml\n`,
    "utf8",
  );
  console.log(`Готово: ${count} страниц, sitemap.xml, robots.txt`);
}

main();
