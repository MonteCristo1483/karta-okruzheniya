"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry, Position } from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
import BuildingSheet, { RED_FLAG_PENALTY } from "./building-sheet";
import type { BuildingBreakdown, SelectedBuilding } from "./building-sheet";
import LayersPanel, { DEFAULT_VISIBILITY } from "./layers-panel";
import type { Visibility } from "./layers-panel";
import SearchBox from "./search-box";
import type { SearchItem } from "./search-box";
import { geometryCenter, haversineM, nearestPosition } from "./geo-utils";
import { ICON_PATHS, type IconKey } from "./icons";
import { initialTheme, THEME_KEY } from "./theme";
import type { Theme } from "./theme";

// Пути относительные — статический экспорт живёт в подпапке
// basePath (/karta-okruzheniya на GitHub Pages).
const BUILDINGS_URL = "data/presnya_buildings.geojson";
const POI_URL = "data/presnya_poi.geojson";
const REDZONES_URL = "data/presnya_redzones.geojson";

// Подложка: компактный GeoJSON, собранный из OSM (pipeline/build_basemap.py).
// Только улицы, вода, парки и землепользование — без зданий, POI и адресов,
// поэтому файл лёгкий, работает офлайн (service worker) и не зависит от
// внешних тайл-серверов. Подписи улиц — DOM-текст (крисп на Retina/iPhone).
const BASEMAP_URL = "data/basemap.geojson";

// Воркер maplibre отдаём статикой из public/ — bundlers не резолвят
// его динамический URL (Turbopack и webpack одинаково).
maplibregl.setWorkerUrl("maplibre-gl-worker.mjs");

// Палитры карты по темам (мягкие акценты, не неон — SPEC).
const MAP_THEMES = {
  light: {
    vlow: "#9fb0c9",
    low: "#a9b9d4",
    mid: "#b4c4de",
    high: "#b7cfd6",
    vhigh: "#b2d6c3",
    selected: "#7fe0a0",
    dim: "#c5cedb",
    halo: "#54d989",
    outlineCore: "#28b96a",
    redzone: "#cf8c80",
    route: "#2fb27a",
    poiGreen: "#0e9f6e",
    poiMetro: "#e11d48",
    iso: "#2f7ff0",
    pill: "bg-white/90 text-slate-700",
    redPill: "bg-white/95 text-red-700",
    redIcon: "#b91c1c",
    // Подложка (прохладная диорама дня)
    land: "#d8e3ef",
    landUse: "#d0dcea",
    park: "#bfe0c2",
    wood: "#aed4b2",
    water: "#72c8e8",
    waterLine: "#4aa8cf",
    roadMinor: "#fbfdff",
    roadCasing: "#b9c8d8",
    roadMajor: "#ffffff",
    label: "#2f3a4a",
    labelHalo: "#ffffff",
    labelWater: "#2f7fa8",
    labelWaterHalo: "#ffffff",
    tree: "#6fae72",
    treeDark: "#4f8f5a",
    shadow: "#1a2430",
  },
  dark: {
    vlow: "#4c5a70",
    low: "#54637c",
    mid: "#3a4b66",
    high: "#3f5c70",
    vhigh: "#4f7d68",
    selected: "#6fe0a6",
    dim: "#2a3340",
    halo: "#7fe0aa",
    outlineCore: "#8ef0b4",
    redzone: "#8f5b55",
    route: "#5cc8be",
    poiGreen: "#7fb98a",
    poiMetro: "#ef8fa4",
    iso: "#38bdf8",
    pill: "bg-slate-800/90 text-slate-200",
    redPill: "bg-slate-800/95 text-red-300",
    redIcon: "#f87171",
    // Подложка (тёмный город)
    land: "#141b24",
    landUse: "#19212c",
    park: "#1d3a2b",
    wood: "#1a3326",
    water: "#0d3f63",
    waterLine: "#1a5f8a",
    roadMinor: "#2a3542",
    roadCasing: "#3b4756",
    roadMajor: "#4c5a6b",
    label: "#e2e8f0",
    labelHalo: "#0b1220",
    labelWater: "#82c4e8",
    labelWaterHalo: "#0b1220",
    tree: "#2f6b4a",
    treeDark: "#1e4a33",
    shadow: "#000000",
  },
} as const;

type Palette = { [K in keyof (typeof MAP_THEMES)["light"]]: string };

// Свет сцены: тёплый день / холодная ночь. MapLibre light для fill-extrusion.
type ThemeLight = {
  anchor: "viewport";
  color: string;
  intensity: number;
  position: [number, number, number];
};

const MAP_LIGHT: Record<Theme, ThemeLight> = {
  light: {
    anchor: "viewport",
    color: "#ffffff",
    intensity: 0.2,
    position: [1.2, 210, 35],
  },
  dark: {
    anchor: "viewport",
    color: "#bcd7ff",
    intensity: 0.3,
    position: [1.2, 210, 35],
  },
};

// Контактная тень под домами: на тёмной теме заметнее.
const BUILDING_SHADOW_OPACITY: Record<Theme, number> = { light: 0.22, dark: 0.4 };

// FX-2: пороги цвета — квантили реального распределения индекса.
// Жёсткий step 5/7 после скоринга v4 давал ~56% зданий одним бежевым
// (бэклог FX №2). Линейный градиент в RGB не подошёл: смешение пастелей
// даёт выцветшие промежуточные тона. Поэтому — 5 ступеней (квинтили),
// каждая по ~20% зданий, соседние оттенки близки по тону, но насыщенные.
// Цвет — презентация: формула индекса (scoring.json) не затрагивается.
// null = данных мало, фолбэк на старый step 5/7.
type ColorStops = { t1: number; t2: number; t3: number; t4: number; mid: number };

const buildingColorExpr = (p: Palette, s: ColorStops | null) =>
  [
    "case",
    ["to-boolean", ["feature-state", "selected"]],
    p.selected,
    ["to-boolean", ["feature-state", "inactive"]],
    p.dim,
    s
      ? // step: v < t1 → vlow … v ≥ t4 → vhigh; null-индекс → mid.
        [
          "step",
          ["coalesce", ["get", "index"], s.mid],
          p.vlow,
          s.t1,
          p.low,
          s.t2,
          p.mid,
          s.t3,
          p.high,
          s.t4,
          p.vhigh,
        ]
      : ["step", ["get", "index"], p.low, 5, p.mid, 7, p.high],
  ] as never;

// ── Подложка (локальный GeoJSON из OSM) ──────────────────────────────────
// Одна фича = один объект; layer: landuse | green | water | river |
// waterway | road. Свойства намеренно минимальны (class, name).
type BasemapProps = { layer: string; class: string; name?: string };

const BM_LAYER = (layer: string) => ["==", ["get", "layer"], layer] as never;
const BM_CLASS = (cls: string) => ["==", ["get", "class"], cls] as never;
const BM_ROAD = (cls: string) => ["all", BM_LAYER("road"), BM_CLASS(cls)] as never;

// Нижние слои подложки: земля, зелень, вода, дороги. Порядок важен —
// здания и оверлеи проекта добавляются выше.
function basemapLayers(p: Palette): maplibregl.LayerSpecification[] {
  return [
    {
      id: "bm-land",
      type: "background",
      paint: { "background-color": p.land },
    },
    {
      id: "bm-landuse",
      type: "fill",
      source: "basemap",
      filter: BM_LAYER("landuse"),
      paint: { "fill-color": p.landUse, "fill-opacity": 0.75 },
    },
    {
      id: "bm-green",
      type: "fill",
      source: "basemap",
      filter: BM_LAYER("green"),
      paint: {
        "fill-color": ["match", ["get", "class"], "wood", p.wood, p.park],
        "fill-opacity": 0.9,
      },
    },
    {
      // Деревья — декоративные точки в парках (оформление, не факт).
      id: "bm-trees",
      type: "circle",
      source: "trees",
      paint: {
        "circle-color": p.tree,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.8, 15, 3.1, 17, 5.2],
        "circle-stroke-color": p.treeDark,
        "circle-stroke-width": 0.5,
        "circle-opacity": 0.95,
        "circle-blur": 0.1,
      },
    },
    {
      id: "bm-water",
      type: "fill",
      source: "basemap",
      filter: BM_LAYER("water"),
      paint: {
        "fill-color": p.water,
        "fill-outline-color": p.waterLine,
        "fill-antialias": true,
      },
    },
    {
      // Москва-река в OSM — осевая линия, рисуем широкой лентой.
      id: "bm-river",
      type: "line",
      source: "basemap",
      filter: BM_LAYER("river"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.water,
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 6, 14, 24, 15, 48, 16, 96, 17, 190],
        "line-blur": 1.5,
      },
    },
    {
      id: "bm-waterway",
      type: "line",
      source: "basemap",
      filter: BM_LAYER("waterway"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.waterLine,
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.8, 16, 3],
      },
    },
    {
      id: "bm-road-minor-casing",
      type: "line",
      source: "basemap",
      minzoom: 12,
      filter: BM_ROAD("minor"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadCasing,
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.6, 14, 3, 16, 5, 18, 7.5],
      },
    },
    {
      id: "bm-road-minor",
      type: "line",
      source: "basemap",
      minzoom: 11,
      filter: BM_ROAD("minor"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadMinor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.9, 14, 1.9, 16, 3.4, 18, 6],
      },
    },
    {
      id: "bm-road-casing",
      type: "line",
      source: "basemap",
      minzoom: 10,
      filter: BM_ROAD("major"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadCasing,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2.8, 14, 5.5, 16, 9.5, 18, 16],
      },
    },
    {
      id: "bm-road-major",
      type: "line",
      source: "basemap",
      minzoom: 10,
      filter: BM_ROAD("major"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadMajor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.6, 14, 3.4, 16, 6.2, 18, 11],
      },
    },
  ];
}

// Цвета подложки, которые меняются при переключении темы.
// Данные те же — перекрашиваем только paint.
function basemapPaint(p: Palette): Record<string, Record<string, unknown>> {
  return {
    "bm-land": { "background-color": p.land },
    "bm-landuse": { "fill-color": p.landUse },
    "bm-green": { "fill-color": ["match", ["get", "class"], "wood", p.wood, p.park] },
    "bm-trees": { "circle-color": p.tree, "circle-stroke-color": p.treeDark },
    "bm-water": { "fill-color": p.water, "fill-outline-color": p.waterLine },
    "bm-river": { "line-color": p.water },
    "bm-waterway": { "line-color": p.waterLine },
    "bm-road-minor-casing": { "line-color": p.roadCasing },
    "bm-road-minor": { "line-color": p.roadMinor },
    "bm-road-casing": { "line-color": p.roadCasing },
    "bm-road-major": { "line-color": p.roadMajor },
    "glow-outer": { "circle-color": p.selected },
    "glow-inner": { "circle-color": p.selected },
    "buildings-shadow": { "fill-color": p.shadow },
  };
}

type PoiProps = { type: string; subtype: string | null; name: string | null };
type RedzoneProps = { kind: string; class: string | null };
// Пешие маршруты (Valhalla) — файл на здание, грузится при выборе.
type BuildingRoutes = {
  categories: Record<
    string,
    { minutes: number; distance_m: number; geometry: { type: "LineString"; coordinates: Position[] } }
  >;
};
type FlatRoutes = Record<string, { minutes: number; coordinates: Position[] }>;
const ROUTES_URL = (id: number) => `data/routes/${id}.json`;
// Изохроны 5/10/15 минут — файл на здание, грузится при выборе (как routes/).
type IsoGeometry = { type: "Polygon" | "MultiPolygon"; coordinates: unknown };
type IsochroneData = Record<string, IsoGeometry>;
const ISOCHRONE_URL = (id: number) => `data/isochrones/${id}.json`;
type GFeature<P> = { type: "Feature"; geometry: Geometry; properties: P };
type GCollection<P> = { type: "FeatureCollection"; features: GFeature<P>[] };

// Подписи подложки — DOM-маркеры, а не symbol-слой: не нужны глиф-серверы
// и PBF-шрифты (экономия первой загрузки), текст крисп на Retina.
type LabelCandidate = {
  name: string;
  kind: "street" | "water" | "park";
  importance: number;
  a: Position;
  b: Position;
};

function longestSegment(geom: Geometry): { a: Position; b: Position } | null {
  let lines: Position[][] = [];
  if (geom.type === "LineString") lines = [geom.coordinates];
  else if (geom.type === "MultiLineString") lines = geom.coordinates;
  else return null;
  let best: { a: Position; b: Position; score: number } | null = null;
  for (const line of lines) {
    for (let i = 1; i < line.length; i++) {
      const a = line[i - 1];
      const b = line[i];
      const dLat = b[1] - a[1];
      const dLon = (b[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180);
      const score = dLat * dLat + dLon * dLon;
      if (!best || score > best.score) best = { a, b, score };
    }
  }
  return best;
}

// Один кандидат на имя: самые длинные и важные улицы, река/пруды, парки.
function ringContains(pt: Position, ring: Position[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    const cross = yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi || 1e-12) + xi;
    if (cross) inside = !inside;
  }
  return inside;
}

function polygonContains(pt: Position, rings: Position[][]): boolean {
  if (!ringContains(pt, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) {
    if (ringContains(pt, rings[i])) return false;
  }
  return true;
}

// Декоративные деревья в парках: ровная сетка с детерминированным
// смещением, чтобы зелень не выглядела плоским пятном (как на макетах).
// Это оформление, не факт из данных: в карточку и индекс не попадает.
function buildTreePoints(features: GFeature<BasemapProps>[]): GCollection<{ size: number }> {
  const points: GFeature<{ size: number }>[] = [];
  let seed = 20261119;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  for (const f of features) {
    if (f.properties.layer !== "green" || f.properties.class !== "park") continue;
    let polys: Position[][][] = [];
    if (f.geometry.type === "Polygon") polys = [f.geometry.coordinates];
    else if (f.geometry.type === "MultiPolygon") polys = f.geometry.coordinates;
    else continue;
    for (const rings of polys) {
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const ring of rings) {
        for (const [x, y] of ring) {
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
        }
      }
      const area = (maxX - minX) * (maxY - minY);
      if (area < 1e-6) continue; // меньше ~0,7 га
      // 1 дерево на ~45 м² площади рамки — визуальная плотность, не факт.
      const want = Math.min(170, Math.max(6, Math.round(area / 2.1e-7)));
      let made = 0;
      let tries = 0;
      while (made < want && tries < want * 10) {
        tries++;
        const pt: [number, number] = [
          minX + rand() * (maxX - minX),
          minY + rand() * (maxY - minY),
        ];
        if (!polygonContains(pt, rings)) continue;
        points.push({
          type: "Feature",
          properties: { size: 0.8 + rand() * 0.55 },
          geometry: { type: "Point", coordinates: pt },
        });
        made++;
        if (points.length >= 1500) return { type: "FeatureCollection", features: points };
      }
    }
  }
  return { type: "FeatureCollection", features: points };
}

function buildLabelCandidates(features: GFeature<BasemapProps>[]): LabelCandidate[] {
  const byName: Record<string, LabelCandidate> = {};
  const put = (key: string, candidate: LabelCandidate) => {
    const prev = byName[key];
    if (!prev || candidate.importance > prev.importance) byName[key] = candidate;
  };
  for (const f of features) {
    const { layer, class: cls, name } = f.properties;
    if (!name) continue;
    if (layer === "road") {
      if (cls !== "major" && cls !== "minor") continue;
      const seg = longestSegment(f.geometry);
      if (!seg) continue;
      const dLat = seg.b[1] - seg.a[1];
      const dLon = (seg.b[0] - seg.a[0]) * Math.cos((seg.a[1] * Math.PI) / 180);
      const length = Math.hypot(dLat, dLon);
      if (length < 0.00025) continue; // короче ~30 м подпись не нужна
      put(`street:${name}`, {
        name,
        kind: "street",
        importance: (cls === "major" ? 3 : 1) + Math.min(2, length * 100),
        a: seg.a,
        b: seg.b,
      });
    } else if (layer === "river") {
      const seg = longestSegment(f.geometry);
      if (seg) put(`water:${name}`, { name, kind: "water", importance: 2.5, a: seg.a, b: seg.b });
    } else if (layer === "water") {
      const c = geometryCenter(f.geometry);
      if (c) put(`water:${name}`, { name, kind: "water", importance: 1.8, a: c, b: c });
    } else if (layer === "green" && cls === "park") {
      const c = geometryCenter(f.geometry);
      if (c) put(`park:${name}`, { name, kind: "park", importance: 0.6, a: c, b: c });
    }
  }
  return Object.values(byName);
}

type BuildingProps = {
  id: number;
  index?: number;
  address?: string;
  year?: number | null;
  breakdown?: BuildingBreakdown;
};

// Порядок важен для стабильного типа percentiles в карточке.
const PENALTY_KINDS = ["road", "railway", "construction", "industrial"] as const;

const POI_LABELS: Record<string, string> = {
  school: "Школа",
  kindergarten: "Сад",
  park: "Парк",
  subway_entrance: "Метро",
  rail_platform: "Электричка",
  shop: "Магазин",
};

function poiColor(type: string, p: Palette): string {
  return type === "subway_entrance" || type === "rail_platform" ? p.poiMetro : p.poiGreen;
}

// POI type -> ключ слоя-чекбокса / ключа бонуса в breakdown
const POI_TO_LAYER: Record<string, keyof Visibility> = {
  school: "schools",
  kindergarten: "kindergartens",
  park: "parks",
  subway_entrance: "metro",
  rail_platform: "metro",
  shop: "shops",
};

const POI_TO_BONUS: Record<string, string> = {
  school: "schools",
  kindergarten: "kindergartens",
  park: "parks",
  subway_entrance: "transport",
  rail_platform: "transport",
  shop: "shops",
};

const BONUS_KEYS = ["transport", "schools", "parks", "kindergartens", "shops"];

// Бонус -> ключ слоя-чекбокса: маршрут категории рисуется только когда
// включён её слой (FX-1: роут исчезает вместе со слоем категории).
const BONUS_TO_LAYER: Record<string, keyof Visibility> = {
  transport: "metro",
  schools: "schools",
  parks: "parks",
  kindergartens: "kindergartens",
  shops: "shops",
};

const PENALTY_LABELS: Record<string, string> = {
  road: "Магистраль",
  railway: "Ж/д",
  construction: "Стройка",
  industrial: "Промзона",
};

const PENALTY_TO_KIND: Record<string, string> = {
  road: "road",
  railway: "railway",
  construction: "construction",
  industrial: "industrial",
};

function pillBase(extra: string): HTMLDivElement {
  const el = document.createElement("div");
  el.className = `flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] shadow ${extra}`;
  return el;
}

// POI на карте — иконка в «перевернутой капле» (как на макетах /design).
// Подпись («Школа · 7 мин») — только у ближайшего к выбранному дому POI.
const POI_TO_ICON: Record<string, IconKey> = {
  school: "school",
  kindergarten: "kindergarten",
  park: "park",
  subway_entrance: "subway_entrance",
  rail_platform: "rail_platform",
  shop: "shop",
};

function makePoiPinEl(
  icon: IconKey,
  color: string,
  label: string | null,
  pill: string,
): HTMLDivElement {
  const NS = "http://www.w3.org/2000/svg";
  const el = document.createElement("div");
  el.className = "flex items-center gap-1";
  el.style.zIndex = "2";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", "24");
  svg.setAttribute("height", "24");
  svg.setAttribute("viewBox", "0 0 24 24");
  const pin = document.createElementNS(NS, "path");
  pin.setAttribute(
    "d",
    "M12 1.5C7.3 1.5 3.5 5.3 3.5 10c0 5.6 8.5 12.6 8.5 12.6S20.5 15.6 20.5 10c0-4.7-3.8-8.5-8.5-8.5z",
  );
  pin.setAttribute("fill", color);
  pin.setAttribute("stroke", "#ffffff");
  pin.setAttribute("stroke-width", "1.4");
  const hole = document.createElementNS(NS, "circle");
  hole.setAttribute("cx", "12");
  hole.setAttribute("cy", "10");
  hole.setAttribute("r", "5.2");
  hole.setAttribute("fill", "#ffffff");
  const iconPath = document.createElementNS(NS, "path");
  iconPath.setAttribute("d", ICON_PATHS[icon]);
  // Иконка рисуется в 24×24 — сжимаем в белый круг капли.
  iconPath.setAttribute("transform", "translate(12 10) scale(0.4) translate(-12 -12)");
  iconPath.setAttribute("fill", "none");
  iconPath.setAttribute("stroke", color);
  iconPath.setAttribute("stroke-width", "2");
  iconPath.setAttribute("stroke-linecap", "round");
  iconPath.setAttribute("stroke-linejoin", "round");
  svg.append(pin, hole, iconPath);
  el.append(svg);
  if (label) {
    const span = document.createElement("span");
    span.className = `rounded-full px-2 py-0.5 text-[11px] shadow ${pill}`;
    span.textContent = label;
    el.append(span);
  }
  return el;
}

function makeRedPinEl(text: string, pill: string, iconColor: string): HTMLDivElement {
  const el = pillBase(`${pill} red-pin`);
  el.style.zIndex = "2";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "12");
  svg.setAttribute("height", "12");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M8 1.5 15 14H1L8 1.5Z");
  path.setAttribute("stroke", iconColor);
  path.setAttribute("stroke-width", "1.6");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  const label = document.createElement("span");
  label.textContent = text;
  el.append(svg, label);
  return el;
}

// Маршрут «по крышам» → мягкая дуга: квадратичная кривая Безье, контрольная
// точка сбоку от середины (линия огибает здания). Прямые отрезки не подходят —
// сэмплируем кривую в полилинию.
function curvedLine(a: Position, b: Position, bend = 0.25): Position[] {
  const mx = (a[0] + b[0]) / 2;
  const my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  const pts: Position[] = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24;
    const u = 1 - t;
    pts.push([
      u * u * a[0] + 2 * u * t * cx + t * t * b[0],
      u * u * a[1] + 2 * u * t * cy + t * t * b[1],
    ]);
  }
  return pts;
}

export default function Map() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<SelectedBuilding | null>(null);
  const [visible, setVisible] = useState<Visibility>(DEFAULT_VISIBILITY);
  const [searchItems, setSearchItems] = useState<SearchItem[]>([]);
  const [deepLinkError, setDeepLinkError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("light"); // стабильно для SSR/гидрации
  const [mounted, setMounted] = useState(false);
  const [fatalError, setFatalError] = useState<string | null>(null);
  // Splash: "show" → "fade" (карта готова или 2 с) → "gone".
  const [splash, setSplash] = useState<"show" | "fade" | "gone">("show");
  const deselectRef = useRef<(() => void) | null>(null);
  const selectRef = useRef<((id: number) => void) | null>(null);
  const applyVisibilityRef = useRef<((v: Visibility) => void) | null>(null);
  const applyThemeRef = useRef<((t: Theme) => void) | null>(null);
  const selectedRef = useRef<SelectedBuilding | null>(null);
  const visibleRef = useRef<Visibility>(DEFAULT_VISIBILITY);
  // Маршруты выбранного здания (Valhalla) и кэш по id.
  // TS7 (tsgo): глобальный new Map<K,V>() ломается TS7009 — Record (decisions.md).
  // undefined = не запрашивали, null = файла нет (не повторять), иначе маршруты.
  const routesRef = useRef<FlatRoutes | null>(null);
  const routesCacheRef = useRef<Record<number, FlatRoutes | null>>({});
  // Изохрона: минуты выбраны в карточке, геометрия — по файлу дома.
  const [contour, setContour] = useState<number | null>(null);
  const isoCacheRef = useRef<Record<number, IsochroneData | null>>({});
  const isoGeomRef = useRef<IsoGeometry | null>(null);
  const setIsoSourceRef = useRef<((geom: IsoGeometry | null) => void) | null>(null);
  // Реальная тема для карты доступна сразу (карта — клиент-only).
  const themeRef = useRef<Theme>(initialTheme());

  const updateSelected = (b: SelectedBuilding | null) => {
    selectedRef.current = b;
    setSelected(b);
  };

  // Splash: после fade убираем элемент из DOM.
  useEffect(() => {
    if (splash !== "fade") return;
    const t = window.setTimeout(() => setSplash("gone"), 350);
    return () => window.clearTimeout(t);
  }, [splash]);

  useEffect(() => {
    visibleRef.current = visible;
    applyVisibilityRef.current?.(visible);
  }, [visible]);

  // Изохрона выбранного дома: файл грузим один раз, кэшируем по id.
  // null в contour — зона выключена; смена дома на том же времени просто
  // перерисовывает другую геометрию.
  useEffect(() => {
    let cancelled = false;
    const id = selected?.id ?? null;
    const apply = (data: IsochroneData | null) => {
      if (cancelled) return;
      const geom =
        id !== null && contour !== null ? data?.[String(contour)] ?? null : null;
      isoGeomRef.current = geom;
      setIsoSourceRef.current?.(geom);
    };
    if (id === null || contour === null) {
      apply(null);
      return;
    }
    const cached = isoCacheRef.current[id];
    if (cached !== undefined) {
      apply(cached);
      return;
    }
    fetch(ISOCHRONE_URL(id))
      .then((res) => (res.ok ? res.json() : null))
      .then((data: IsochroneData | null) => {
        isoCacheRef.current[id] = data;
        apply(data);
      })
      .catch(() => {
        isoCacheRef.current[id] = null;
        apply(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id, contour]);

  // После монтирования синхронизируемся с localStorage (антфлеш-скрипт
  // в layout уже выставил data-theme; здесь подхватываем UI).
  useEffect(() => {
    const real = initialTheme();
    themeRef.current = real;
    setMounted(true);
    setTheme(real);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    themeRef.current = theme;
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // приватный режим — тема просто не сохранится
    }
    applyThemeRef.current?.(theme);
  }, [theme, mounted]);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    let cancelled = false;
    let map: maplibregl.Map | null = null;

    const init = async () => {
      const fetchJson = async (url: string): Promise<unknown> => {
        let lastErr: unknown = null;
        for (let attempt = 0; attempt < 2; attempt++) {
          try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status} по ${url}`);
            return await res.json();
          } catch (e) {
            lastErr = e;
          }
        }
        throw lastErr;
      };

      let buildings: GCollection<BuildingProps>;
      let poi: GCollection<PoiProps>;
      let redzones: GCollection<RedzoneProps>;
      let basemapPromise: Promise<GCollection<BasemapProps>>;
      try {
        // Подложка не критична и грузится параллельно: карта с домами
        // открывается, не дожидаясь её. Если файла нет — просто без улиц.
        [buildings, poi, redzones] = (await Promise.all([
          fetchJson(BUILDINGS_URL),
          fetchJson(POI_URL),
          fetchJson(REDZONES_URL),
        ])) as [GCollection<BuildingProps>, GCollection<PoiProps>, GCollection<RedzoneProps>];
        basemapPromise = fetchJson(BASEMAP_URL)
          .then((data) => data as GCollection<BasemapProps>)
          .catch((e) => {
            console.warn("Подложка не загрузилась:", e);
            return { type: "FeatureCollection", features: [] } as GCollection<BasemapProps>;
          });
      } catch (e) {
        console.error("Не удалось загрузить данные карты:", e);
        setFatalError(
          "Не удалось загрузить данные карты. Проверьте соединение и обновите страницу.",
        );
        return;
      }
      if (cancelled) return;
      const palette = MAP_THEMES[themeRef.current];

      // Стопы градиента цвета — по квантилям загруженных индексов (FX-2).
      const indexValues = buildings.features
        .map((f) => (f.properties as { index?: number }).index)
        .filter((x): x is number => typeof x === "number" && Number.isFinite(x))
        .sort((a, b) => a - b);
      const colorStops: ColorStops | null =
        indexValues.length >= 10
          ? (() => {
              const at = (p: number) =>
                indexValues[Math.min(indexValues.length - 1, Math.floor(p * indexValues.length))];
              return {
                t1: at(0.2),
                t2: at(0.4),
                t3: at(0.6),
                t4: at(0.8),
                mid: at(0.5),
              };
            })()
          : null;

      const poiCenters = poi.features
        .map((f) => ({ f, center: geometryCenter(f.geometry) }))
        .filter(
          (x): x is { f: GFeature<PoiProps>; center: [number, number] } =>
            x.center !== null,
        );
      const redzoneFeatures = redzones.features;
      const buildingCenters: Record<number, [number, number]> = {};
      for (const f of buildings.features) {
        const c = geometryCenter(f.geometry);
        if (c) buildingCenters[f.properties.id] = c;
      }

      let labelCandidates: LabelCandidate[] = [];

      // Поиск по адресу: только здания, у которых адрес есть в OSM
      // (сейчас 2 511 из 3 306). Остальные находятся кликом по карте.
      const searchIndex: SearchItem[] = [];
      for (const f of buildings.features) {
        if (!f.properties.address) continue;
        searchIndex.push({
          id: f.properties.id,
          address: f.properties.address,
          index: f.properties.index ?? null,
          year: f.properties.year ?? null,
        });
      }
      if (!cancelled) setSearchItems(searchIndex);

      // Перцентили по штрафным слоям: доля домов, которые находятся дальше
      // от объекта (тише), 0..1. Считаем один раз по загруженному geojson,
      // чтобы карточка могла сказать «хуже 80% домов Пресни».
      const penaltyDistances: Record<string, number[]> = {};
      for (const kind of PENALTY_KINDS) {
        penaltyDistances[kind] = buildings.features.map(
          (f) =>
            f.properties.breakdown?.penalties?.[kind]?.distance_m ??
            Number.POSITIVE_INFINITY,
        );
      }
      const percentileFor = (kind: string, distance: number): number => {
        const values = penaltyDistances[kind];
        if (!values?.length) return 0;
        let farther = 0;
        for (const d of values) if (d > distance) farther++;
        return farther / values.length;
      };

      const nearestPoi = (
        bonusKey: string,
        from: [number, number],
      ): { f: GFeature<PoiProps>; center: [number, number] } | null => {
        let best: { f: GFeature<PoiProps>; center: [number, number] } | null = null;
        let bestDist = Infinity;
        for (const { f, center } of poiCenters) {
          if (POI_TO_BONUS[f.properties.type] !== bonusKey) continue;
          const d = haversineM(center, from);
          if (d < bestDist) {
            bestDist = d;
            best = { f, center };
          }
        }
        return best;
      };

      const routesData: FeatureCollection = {
        type: "FeatureCollection",
        features: [],
      };

      let poiMarkers: maplibregl.Marker[] = [];
      let redPins: maplibregl.Marker[] = [];

      try {
        map = new maplibregl.Map({
        container,
        style: {
          version: 8,
          light: MAP_LIGHT[themeRef.current],
          sources: {
            basemap: {
              type: "geojson",
              data: { type: "FeatureCollection", features: [] },
            },
            trees: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
            glow: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
            buildings: { type: "geojson", data: buildings, promoteId: "id" },
            redzones: { type: "geojson", data: redzones },
            routes: { type: "geojson", data: routesData },
            isochrone: {
              type: "geojson",
              data: { type: "FeatureCollection", features: [] },
            },
          },
          layers: [
            // Полноценная подложка: земля, парки, вода, улицы (локальный OSM).
            ...basemapLayers(palette),
            {
              id: "redzones-fill",
              type: "fill",
              source: "redzones",
              filter: ["==", ["geometry-type"], "Polygon"],
              paint: { "fill-color": palette.redzone, "fill-opacity": 0.09 },
            },
            {
              id: "redzones-road",
              type: "line",
              source: "redzones",
              filter: ["==", ["get", "kind"], "road"],
              paint: {
                "line-color": palette.redzone,
                "line-width": 1.6,
                "line-opacity": 0.35,
              },
            },
            {
              // Ж/д — фоновый слой: тонкий пунктир, чтобы не шумел (FX-3).
              id: "redzones-rail",
              type: "line",
              source: "redzones",
              filter: ["==", ["get", "kind"], "railway"],
              paint: {
                "line-color": palette.redzone,
                "line-width": 1.2,
                "line-opacity": 0.3,
                "line-dasharray": [3, 3],
              },
            },
            {
              // Изохрона — под зданиями, чтобы 3D-дома не перекрывались.
              id: "isochrone-fill",
              type: "fill",
              source: "isochrone",
              paint: {
                "fill-color": palette.iso,
                "fill-opacity": 0.14,
              },
            },
            {
              id: "isochrone-line",
              type: "line",
              source: "isochrone",
              paint: {
                "line-color": palette.iso,
                "line-width": 1.5,
                "line-opacity": 0.8,
              },
            },
            {
              // Мягкое свечение под выбранным домом (как на макетах).
              id: "glow-outer",
              type: "circle",
              source: "glow",
              paint: {
                "circle-color": palette.selected,
                "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 26, 17, 70],
                "circle-opacity": 0.14,
                "circle-blur": 1,
              },
            },
            {
              id: "glow-inner",
              type: "circle",
              source: "glow",
              paint: {
                "circle-color": palette.selected,
                "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 12, 17, 34],
                "circle-opacity": 0.3,
                "circle-blur": 0.8,
              },
            },
            {
              // Контактная тень: плоская копия пятна дома со сдвигом.
              id: "buildings-shadow",
              type: "fill",
              source: "buildings",
              paint: {
                "fill-color": palette.shadow,
                "fill-opacity": BUILDING_SHADOW_OPACITY[themeRef.current],
                "fill-translate": [4, 5],
                "fill-translate-anchor": "viewport",
              },
            },
            {
              id: "buildings",
              type: "fill-extrusion",
              source: "buildings",
              paint: {
                // fill-extrusion-color поддерживает feature-state,
                // fill-extrusion-opacity — нет, поэтому затемнение через цвет.
                "fill-extrusion-color": buildingColorExpr(palette, colorStops),
                "fill-extrusion-height": ["coalesce", ["get", "height"], 9],
                "fill-extrusion-base": 0,
                "fill-extrusion-opacity": 0.95,
                "fill-extrusion-vertical-gradient": true,
              },
            },
            {
              id: "outline-halo",
              type: "line",
              source: "buildings",
              paint: {
                "line-color": palette.halo,
                "line-width": 14,
                "line-opacity": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0.45,
                  0,
                ],
              },
            },
            {
              id: "outline-core",
              type: "line",
              source: "buildings",
              paint: {
                "line-color": palette.outlineCore,
                "line-width": 3,
                "line-opacity": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0.95,
                  0,
                ],
              },
            },
            {
              id: "routes-line",
              type: "line",
              source: "routes",
              paint: {
                "line-color": palette.route,
                "line-width": 2,
                "line-opacity": 0.7,
                "line-dasharray": [4, 4],
              },
            },
          ],
        },
        // Демонстрационный ракурс «диорама»: плотный квартал, парк и река.
        center: [37.554, 55.756],
        zoom: 15.35,
        pitch: 55,
        bearing: -25,
        maxPitch: 72,
        attributionControl: false,
        });
      } catch (e) {
        console.error("WebGL-контекст не создан:", e);
        if (!cancelled) {
          setFatalError(
            "Не удалось запустить WebGL2. Проверьте, что браузер и устройство поддерживают WebGL2, и обновите страницу.",
          );
        }
        return;
      }

      map.addControl(new maplibregl.NavigationControl(), "top-right");

      // Подписи улиц/воды: DOM-маркеры (без глиф-PBF). Пересобираем после
      // движения карты: за время анимации маркеры едут вместе с точкой.
      let labelMarkers: maplibregl.Marker[] = [];
      const rebuildLabels = () => {
        if (!map) return;
        labelMarkers.forEach((m) => m.remove());
        labelMarkers = [];
        const p = MAP_THEMES[themeRef.current];
        const zoom = map.getZoom();
        if (zoom < 12.6) return;
        const size = zoom >= 16 ? 13.5 : zoom >= 14.2 ? 12.5 : 11.5;
        const width = map.getContainer().clientWidth;
        const height = map.getContainer().clientHeight;
        const boxes: { x1: number; y1: number; x2: number; y2: number }[] = [];
        const maxLabels = width < 600 ? 14 : 20;
        const sorted = [...labelCandidates].sort((a, b) => b.importance - a.importance);
        for (const c of sorted) {
          if (labelMarkers.length >= maxLabels) break;
          if (c.kind === "park" && zoom < 15) continue;
          if (c.kind === "water" && zoom < 12.8) continue;
          const mid: [number, number] = [(c.a[0] + c.b[0]) / 2, (c.a[1] + c.b[1]) / 2];
          const pt = map.project(mid);
          const bw = Math.min(240, c.name.length * size * 0.62 + 8);
          const bh = size + 6;
          // Подпись целиком в кадре: половинки слов на краю выглядят как баг.
          if (
            pt.x - bw / 2 < 2 ||
            pt.y - bh / 2 < 2 ||
            pt.x + bw / 2 > width - 2 ||
            pt.y + bh / 2 > height - 2
          ) {
            continue;
          }
          let angle = 0;
          if (c.a[0] !== c.b[0] || c.a[1] !== c.b[1]) {
            const p1 = map.project(c.a as [number, number]);
            const p2 = map.project(c.b as [number, number]);
            angle = (Math.atan2(p2.y - p1.y, p2.x - p1.x) * 180) / Math.PI;
            if (angle > 90) angle -= 180;
            if (angle < -90) angle += 180;
            // Почти вертикальные подписи читаются хуже, чем отсутствие.
            if (Math.abs(angle) > 68) continue;
          }
          const rad = (angle * Math.PI) / 180;
          const boxW = Math.abs(bw * Math.cos(rad)) + Math.abs(bh * Math.sin(rad));
          const boxH = Math.abs(bw * Math.sin(rad)) + Math.abs(bh * Math.cos(rad));
          const box = {
            x1: pt.x - boxW / 2,
            y1: pt.y - boxH / 2,
            x2: pt.x + boxW / 2,
            y2: pt.y + boxH / 2,
          };
          if (
            boxes.some(
              (b) => !(box.x2 < b.x1 || box.x1 > b.x2 || box.y2 < b.y1 || box.y1 > b.y2),
            )
          ) {
            continue;
          }
          boxes.push(box);

          const color =
            c.kind === "water" ? p.labelWater : c.kind === "park" ? p.poiGreen : p.label;
          const halo = c.kind === "water" ? p.labelWaterHalo : p.labelHalo;
          const wrap = document.createElement("div");
          wrap.style.cssText = "pointer-events:none;z-index:0;";
          const inner = document.createElement("div");
          inner.textContent = c.name;
          inner.style.cssText = [
            `transform: rotate(${angle.toFixed(1)}deg)`,
            "white-space: nowrap",
            "font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
            `font-size: ${size}px`,
            "font-weight: 600",
            `font-style: ${c.kind === "water" ? "italic" : "normal"}`,
            `color: ${color}`,
            "line-height: 1",
            "letter-spacing: 0.01em",
            `text-shadow: -1.2px -1.2px 0 ${halo}, 1.2px -1.2px 0 ${halo}, -1.2px 1.2px 0 ${halo}, 1.2px 1.2px 0 ${halo}, 0 0 2px ${halo}`,
          ].join(";");
          wrap.append(inner);
          labelMarkers.push(
            new maplibregl.Marker({ element: wrap, anchor: "center" })
              .setLngLat(mid)
              .addTo(map),
          );
        }
      };
      map.on("moveend", rebuildLabels);
      map.on("resize", rebuildLabels);
      (window as unknown as { __rebuildLabels?: () => void }).__rebuildLabels = rebuildLabels;

      // Подложка приходит параллельно: как только она есть — подставляем
      // данные и подписи, не задерживая открытие карты с домами.
      // setData до готовности стиля MapLibre может проигнорировать, поэтому
      // ждём load и запоминаем данные.
      let styleReady = false;
      let basemapData: GCollection<BasemapProps> | null = null;
      const applyBasemap = () => {
        if (cancelled || !map || !basemapData) return;
        const src = map.getSource("basemap") as maplibregl.GeoJSONSource | null;
        if (!src) return;
        src.setData(basemapData as never);
        labelCandidates = buildLabelCandidates(basemapData.features);
        (map.getSource("trees") as maplibregl.GeoJSONSource | null)?.setData(
          buildTreePoints(basemapData.features) as never,
        );
        rebuildLabels();
      };
      void basemapPromise.then((data) => {
        if (cancelled) return;
        basemapData = data;
        if (styleReady) applyBasemap();
      });

      // Источник изохроны: максимум одна фича (полигон выбранного времени).
      setIsoSourceRef.current = (geom) => {
        const src = map?.getSource("isochrone") as maplibregl.GeoJSONSource | null;
        if (!src) return;
        src.setData({
          type: "FeatureCollection",
          features: geom ? [{ type: "Feature", properties: {}, geometry: geom }] : [],
        } as never);
      };
      map.on("error", (e) => console.error("map error:", e.error ?? e));
      (window as unknown as { __map?: maplibregl.Map }).__map = map;

      // Заставка: убираем, когда карта отрисована (idle) или через 2 с —
      // что раньше.
      const hideSplash = () => setSplash((s) => (s === "show" ? "fade" : s));
      map.once("idle", hideSplash);
      window.setTimeout(hideSplash, 2000);

      const rebuildOverlays = (sel: SelectedBuilding | null, vis: Visibility) => {
        if (!map) return;
        const theme = MAP_THEMES[themeRef.current];
        poiMarkers.forEach((m) => m.remove());
        poiMarkers = [];
        redPins.forEach((m) => m.remove());
        redPins = [];

        const selCenter = sel ? (buildingCenters[sel.id] ?? null) : null;
        const nearestByCat: Record<string, GFeature<PoiProps> | null> = {};
        if (selCenter) {
          for (const cat of BONUS_KEYS) {
            nearestByCat[cat] = nearestPoi(cat, selCenter)?.f ?? null;
          }
        }

        for (const { f, center } of poiCenters) {
          const layerKey = POI_TO_LAYER[f.properties.type];
          if (!layerKey || !vis[layerKey]) continue;
          const bonusKey = POI_TO_BONUS[f.properties.type];
          let label: string | null = null;
          if (sel && selCenter && nearestByCat[bonusKey] === f) {
            const entry = sel.breakdown?.bonuses[bonusKey];
            const minutes = routesRef.current?.[bonusKey]?.minutes ?? entry?.minutes;
            if (minutes != null) {
              label = `${POI_LABELS[f.properties.type] ?? f.properties.type} · ${Math.round(minutes)} мин`;
            }
          }
          poiMarkers.push(
            new maplibregl.Marker({
              element: makePoiPinEl(
                POI_TO_ICON[f.properties.type] ?? "shop",
                poiColor(f.properties.type, theme),
                label,
                theme.pill,
              ),
              anchor: "bottom",
            })
              .setLngLat(center)
              .addTo(map),
          );
        }

        if (sel?.breakdown && selCenter) {
          for (const [key, entry] of Object.entries(sel.breakdown.penalties)) {
            if (!entry || entry.penalty >= RED_FLAG_PENALTY) continue;
            const kind = PENALTY_TO_KIND[key];
            if (!kind) continue;
            let bestPt: Position | null = null;
            let bestDist = Infinity;
            for (const rz of redzoneFeatures) {
              if (rz.properties.kind !== kind) continue;
              const np = nearestPosition(rz.geometry, selCenter);
              if (np && np.dist < bestDist) {
                bestDist = np.dist;
                bestPt = np.point;
              }
            }
            if (!bestPt) continue;
            const d = Math.round(entry.distance_m);
            const text = `${PENALTY_LABELS[key] ?? key} · ${d < 15 ? "рядом" : `${d} м`}`;
            redPins.push(
              new maplibregl.Marker({
                element: makeRedPinEl(text, theme.redPill, theme.redIcon),
                anchor: "bottom",
              })
                .setLngLat(bestPt as [number, number])
                .addTo(map),
            );
          }
        }

        const routeFeatures: Feature[] = [];
        if (selCenter) {
          const cached = routesRef.current;
          for (const cat of BONUS_KEYS) {
            const layerKey = BONUS_TO_LAYER[cat];
            if (layerKey && !vis[layerKey]) continue;
            const target = nearestPoi(cat, selCenter);
            if (!target) continue;
            const entry = cached?.[cat];
            // Реальная геометрия Valhalla; дуга Безье — фолбэк, пока файл
            // маршрутов догружается (или если он недоступен).
            const coordinates = entry?.coordinates ?? curvedLine(selCenter, target.center);
            routeFeatures.push({
              type: "Feature",
              properties: { category: cat },
              geometry: { type: "LineString", coordinates },
            });
          }
        }
        routesData.features = routeFeatures;
        (map.getSource("routes") as maplibregl.GeoJSONSource | null)?.setData(routesData);
      };

      applyVisibilityRef.current = (v: Visibility) => {
        if (!map) return;
        if (map.getLayer("redzones-road")) {
          const rz = v.redzones ? "visible" : "none";
          map.setLayoutProperty("redzones-road", "visibility", rz);
          map.setLayoutProperty("redzones-rail", "visibility", rz);
          map.setLayoutProperty("redzones-fill", "visibility", rz);
        }
        if (map.getLayer("routes-line")) {
          map.setLayoutProperty("routes-line", "visibility", v.routes ? "visible" : "none");
        }
        rebuildOverlays(selectedRef.current, v);
      };

      applyThemeRef.current = (t: Theme) => {
        if (!map || !map.getLayer("buildings")) return;
        const p = MAP_THEMES[t];
        map.setLight(MAP_LIGHT[t]);
        map.setPaintProperty("buildings", "fill-extrusion-color", buildingColorExpr(p, colorStops));
        map.setPaintProperty("buildings-shadow", "fill-opacity", BUILDING_SHADOW_OPACITY[t]);
        map.setPaintProperty("outline-halo", "line-color", p.halo);
        map.setPaintProperty("outline-core", "line-color", p.outlineCore);
        map.setPaintProperty("redzones-fill", "fill-color", p.redzone);
        for (const [id, paint] of Object.entries(basemapPaint(p))) {
          if (!map.getLayer(id)) continue;
          for (const [prop, value] of Object.entries(paint)) {
            map.setPaintProperty(id, prop as never, value as never);
          }
        }
        map.setPaintProperty("redzones-road", "line-color", p.redzone);
        map.setPaintProperty("redzones-rail", "line-color", p.redzone);
        map.setPaintProperty("routes-line", "line-color", p.route);
        map.setPaintProperty("isochrone-fill", "fill-color", p.iso);
        map.setPaintProperty("isochrone-line", "line-color", p.iso);
        rebuildOverlays(selectedRef.current, visibleRef.current);
        rebuildLabels();
      };

      let selectedId: number | null = null;
      const allIds = buildings.features.map((f) => f.properties.id);

      const applySelection = () => {
        if (!map) return;
        for (const id of allIds) {
          map.setFeatureState(
            { source: "buildings", id },
            {
              selected: id === selectedId,
              inactive: selectedId !== null && id !== selectedId,
            },
          );
        }
      };

      const setUrlBuilding = (id: number | null) => {
        const url = new URL(window.location.href);
        if (id === null) url.searchParams.delete("building");
        else url.searchParams.set("building", String(id));
        window.history.replaceState(null, "", url);
      };

      const setGlow = (center: [number, number] | null) => {
        const src = map?.getSource("glow") as maplibregl.GeoJSONSource | null;
        src?.setData({
          type: "FeatureCollection",
          features: center
            ? [
                {
                  type: "Feature",
                  properties: {},
                  geometry: { type: "Point", coordinates: center },
                },
              ]
            : [],
        } as never);
      };

      const deselect = () => {
        selectedId = null;
        routesRef.current = null;
        applySelection();
        updateSelected(null);
        setGlow(null);
        rebuildOverlays(null, visibleRef.current);
        setUrlBuilding(null);
        map?.easeTo({ padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: 300 });
      };
      deselectRef.current = deselect;

      // Маршруты здания: кэш + догрузка. null в кэше = «файла нет, не повторять».
      const loadRoutes = async (id: number) => {
        const cached = routesCacheRef.current[id];
        if (cached !== undefined) {
          routesRef.current = cached;
          if (selectedRef.current?.id === id) {
            rebuildOverlays(selectedRef.current, visibleRef.current);
          }
          return;
        }
        try {
          const res = await fetch(ROUTES_URL(id));
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = (await res.json()) as BuildingRoutes;
          const flat: FlatRoutes = {};
          for (const [cat, e] of Object.entries(data.categories)) {
            flat[cat] = { minutes: e.minutes, coordinates: e.geometry.coordinates };
          }
          routesCacheRef.current[id] = flat;
          routesRef.current = flat;
        } catch (e) {
          console.error(`Маршруты для здания ${id} не загрузились:`, e);
          routesCacheRef.current[id] = null;
          routesRef.current = null;
        }
        if (selectedRef.current?.id === id) {
          rebuildOverlays(selectedRef.current, visibleRef.current);
        }
      };

      const flyToBuilding = (center: [number, number]) => {
        // Ждём две отрисовки, чтобы шторка с новым контентом заняла размер,
        // затем центрируем здание в видимой области карты: на мобиле —
        // padding снизу под шторку, на десктопе — padding справа под док.
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            if (!map) return;
            const sheetEl = document.getElementById("building-sheet");
            const rect = sheetEl?.getBoundingClientRect();
            const desktop = window.matchMedia("(min-width: 768px)").matches;
            map.flyTo({
              center,
              zoom: Math.max(map.getZoom(), 15.2),
              speed: 1.1,
              curve: 1.3,
              padding: desktop
                ? { right: (rect?.width ?? 0) + 24, bottom: 0 }
                : { bottom: (rect?.height ?? 0) + 24 },
            });
          });
        });
      };

      // Общая логика выбора: клик по зданию и открытие по ?building=<osm_id>.
      const selectById = (id: number) => {
        const feature = buildings.features.find((f) => f.properties.id === id);
        if (!feature) return;
        selectedId = id;
        setDeepLinkError(null);
        applySelection();

        const props = feature.properties;
        const distanceOf = (kind: string) =>
          props.breakdown?.penalties?.[kind]?.distance_m ??
          Number.POSITIVE_INFINITY;
        const next: SelectedBuilding = {
          id,
          index: props.index ?? null,
          address: props.address ?? null,
          year: props.year ?? null,
          breakdown: props.breakdown ?? null,
          percentiles: {
            road: percentileFor("road", distanceOf("road")),
            railway: percentileFor("railway", distanceOf("railway")),
            construction: percentileFor("construction", distanceOf("construction")),
            industrial: percentileFor("industrial", distanceOf("industrial")),
          },
        };
        updateSelected(next);
        setUrlBuilding(id);
        routesRef.current = routesCacheRef.current[id] ?? null;
        rebuildOverlays(next, visibleRef.current);
        void loadRoutes(id);

        const center = geometryCenter(feature.geometry);
        setGlow(center);
        if (center) flyToBuilding(center);
      };
      selectRef.current = selectById;

      map.on("load", () => {
        styleReady = true;
        applyBasemap();
        applyThemeRef.current?.(themeRef.current);
        applyVisibilityRef.current?.(visibleRef.current);
        setIsoSourceRef.current?.(isoGeomRef.current);
        rebuildLabels();
        // Публичная ссылка «Отправить клиенту»: ?building=<osm_id>
        const param = new URLSearchParams(window.location.search).get("building");
        if (param !== null) {
          const id = Number(param);
          if (Number.isInteger(id) && allIds.includes(id)) {
            selectById(id);
          } else {
            setDeepLinkError(
              "Дом не найден. Возможно, ссылка устарела — выберите дом на карте или найдите по адресу.",
            );
          }
        }
      });

      map.on("click", (e) => {
        if (!map || !map.getLayer("buildings")) return;
        const feats = map.queryRenderedFeatures(e.point, { layers: ["buildings"] });
        const id = feats.length ? (feats[0].id as number) : null;

        if (id === null || id === selectedId) {
          deselect();
          return;
        }

        selectById(id);
      });

      map.on("mousemove", (e) => {
        if (!map || !map.getLayer("buildings")) return;
        const feats = map.queryRenderedFeatures(e.point, { layers: ["buildings"] });
        map.getCanvas().style.cursor = feats.length ? "pointer" : "";
      });
    };

    void init();

    return () => {
      cancelled = true;
      map?.remove();
      map = null;
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="map-gradient h-full w-full" />
      <LayersPanel
        visibility={visible}
        onToggle={(key) =>
          setVisible((v) => ({ ...v, [key]: !v[key] }))
        }
        theme={theme}
        onThemeToggle={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      />
      <SearchBox
        items={searchItems}
        onSelect={(id) => selectRef.current?.(id)}
      />
      <BuildingSheet
        building={selected}
        onClose={() => deselectRef.current?.()}
        contour={contour}
        onContourChange={setContour}
      />
      {deepLinkError && (
        <div className="absolute bottom-6 left-1/2 z-20 w-[min(92vw,420px)] -translate-x-1/2 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow-xl dark:bg-amber-950/80 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <span className="flex-1">{deepLinkError}</span>
            <button
              type="button"
              onClick={() => setDeepLinkError(null)}
              aria-label="Закрыть"
              className="rounded-full p-0.5 text-amber-600 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
                <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        </div>
      )}
      {fatalError && (
        <div className="absolute inset-x-4 top-1/3 z-20 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-800 shadow-lg dark:bg-red-950/70 dark:text-red-200">
          {fatalError}
        </div>
      )}
      {splash !== "gone" && (
        <div
          className={`absolute inset-0 z-40 flex flex-col items-center justify-center gap-4 bg-[#6a9d76] transition-opacity duration-300 ${
            splash === "fade" ? "opacity-0" : "opacity-100"
          }`}
          aria-hidden
        >
          <svg width="72" height="72" viewBox="0 0 512 512">
            <circle cx="256" cy="256" r="130" fill="none" stroke="#ffffff" strokeWidth="64" />
          </svg>
          <div className="text-lg font-semibold tracking-wide text-white">
            Карта окружения
          </div>
        </div>
      )}
      <div className="pointer-events-none absolute bottom-2 right-3 max-w-md text-right text-[11px] leading-tight text-slate-500 dark:text-slate-400">
        © OpenStreetMap contributors, ODbL · Индекс окружения — справочная
        информация по открытым данным, не является замером или оценкой стоимости
      </div>
    </div>
  );
}
