"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import type { Feature, FeatureCollection, Geometry, Position } from "geojson";
import polygonClipping from "polygon-clipping";
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
const BUILDINGS_URL = "data/buildings.pmtiles";
const BASEMAP_URL = "data/basemap.pmtiles";
const MANIFEST_URL = "data/districts/manifest.json";
const INDEX_URL = "data/index.json.gz";

// Протокол pmtiles регистрируем один раз за жизнь вкладки. Не на уровне
// модуля: static export пререндерится в Node, где нет window и MapLibre.
let pmtilesProtocolReady = false;

// Новый экспорт сжимает индекс, маршруты и изохроны как gzip-файлы,
// а не HTTP Content-Encoding. Используем встроенный браузерный декодер.
async function fetchGzipJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} по ${url}`);
  if (typeof DecompressionStream === "undefined") {
    throw new Error("Браузер не поддерживает gzip-декодирование");
  }
  const compressed = await res.arrayBuffer();
  const stream = new Blob([compressed])
    .stream()
    .pipeThrough(new DecompressionStream("gzip"));
  return (await new Response(stream).json()) as T;
}

type DistrictEntry = {
  id: number;
  name: string;
  bbox: [number, number, number, number];
};

type DistrictManifest = {
  v: number;
  index: { role: string; path: string };
  districts: DistrictEntry[];
};

type CompactSearchIndex = {
  v: number;
  ids: number[];
  lon: number[];
  lat: number[];
  addr: (string | null)[];
};

// Воркер maplibre отдаём статикой из public/ — bundlers не резолвят
// его динамический URL (Turbopack и webpack одинаково).
maplibregl.setWorkerUrl("maplibre-gl-worker.mjs");

// Палитры карты по темам (мягкие акценты, не неон — SPEC).
const MAP_THEMES = {
  light: {
    // Обычные дома: 5 приглушённых ступеней балла (смешаны с нейтральным #c3ccd6 на 60%).
    vlow: "#c8b4b2",
    low: "#cbbfb8",
    mid: "#cbcdc6",
    high: "#b8cbc3",
    vhigh: "#a3c6ba",
    // Выбранный дом: яркий цвет ступени (не приглушать).
    selectedVlow: "#cf8f7c",
    selectedLow: "#d8ab8b",
    selectedMid: "#d8cfae",
    selectedHigh: "#a8c9a7",
    selectedVhigh: "#74bd8f",
    // Крыши обычных домов: почти нейтральные светлые — mix(крыша, #f2f5f8, 0.60).
    roofVlow: "#e4ccd8",
    roofLow: "#ddd9e6",
    roofMid: "#dbe5ec",
    roofHigh: "#c9e0d6",
    roofVhigh: "#add8c3",
    roofDim: "#d8dfea",
    // Нежилые здания: холодный стальной контекст в гамме подложки (на z >= 15.5 приглушён к land).
    otherOverview: "#a4b5c9",
    other: "#aebfd2",
    roofOtherOverview: "#b5c5d8",
    roofOther: "#bac9dc",
    selected: "#38b874",
    // Крыша выбранного дома — цвет его ступени (на 5% светлее), не мятная.
    selectedRoof: "#dfd7b8",
    // В светлой теме выбранный дом без заливки тела на всех зумах (тело красится по яркой ступени балла).
    selectedBody: "#d8cfae",
    dim: "#b8c4d4",
    halo: "#38b874",
    outlineCore: "#289e60",
    redzone: "#e2604a",
    route: "#12b3a6",
    routeCore: "#eafff5",
    poiGreen: "#16a36e",
    poiMetro: "#e11d48",
    iso: "#3b82f6",
    pill: "bg-white/90 text-slate-700",
    redPill: "bg-white/95 text-red-700",
    redIcon: "#b91c1c",
    // Подложка: светлая дневная диорама (земля светлее зданий по тону, улицы светлее земли).
    land: "#c3d2e2",
    landUse: "#cbd8e6",
    park: "#579b68",
    wood: "#428b58",
    water: "#35b8d0",
    waterLine: "#248fa9",
    roadMinor: "#8a99ab",
    roadCasing: "#6d7f94",
    roadMajor: "#53657d",
    roadMajorCasing: "#2f435f",
    roadMark: "#e8edf1",
    label: "#172134",
    labelHalo: "#ffffff",
    labelWater: "#0c4a6e",
    labelWaterHalo: "#ffffff",
    tree: "#4a9960",
    treeDark: "#2f6f43",
    shadow: "#1a2838",
  },
  dark: {
    // Обычные дома: 5 ступеней, смешанных с #3a4760 на 35%.
    vlow: "#46536a",
    low: "#4b5972",
    mid: "#3a4a64",
    high: "#3d556a",
    vhigh: "#486a65",
    // Выбранный дом: неприглушённые ступени (ярче обычных).
    selectedVlow: "#4c5a70",
    selectedLow: "#54637c",
    selectedMid: "#3a4b66",
    selectedHigh: "#3f5c70",
    selectedVhigh: "#4f7d68",
    // Крыши обычных домов в тёмной теме.
    roofVlow: "#655b75",
    roofLow: "#657087",
    roofMid: "#4f627d",
    roofHigh: "#44716e",
    roofVhigh: "#4a8066",
    roofDim: "#2a3340",
    // Нежилые здания в тёмной теме: спокойный тёмно-сланцевый контекст без окон.
    otherOverview: "#2c3646",
    other: "#26303e",
    roofOtherOverview: "#354254",
    roofOther: "#2f3b4c",
    selected: "#5ec98f",
    selectedRoof: "#586a84",
    // Тёмная тема: благородный сланцевый фасад выбранного дома (ярче обычных домов и dim).
    selectedBody: "#50627c",
    dim: "#2a3340",
    halo: "#5ec98f",
    outlineCore: "#58c288",
    redzone: "#ff8a7a",
    route: "#5fe0d0",
    routeCore: "#f2fffb",
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
    roadMajorCasing: "#3b4756",
    roadMark: "#3d4a5c",
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
    // Тёплый направленный свет сбоку (азимут ~245°, полярный ~46°):
    // передние и левые фасады светлые, правые — в тени, что согласуется
    // со сдвигом контактных теней на земле [+x, +y] (вправо-вниз).
    color: "#fff6ea",
    intensity: 0.5,
    position: [1.5, 245, 46],
  },
  dark: {
    anchor: "viewport",
    color: "#bcd7ff",
    intensity: 0.32,
    position: [1.2, 245, 36],
  },
};

// Мягкая многослойная тень под домами (3 слоя со сдвигом [3,3]..[6,6], z >= 14):
// убирает эффект «парящих» коробок без z-fighting и двойных кромок.
const BUILDING_SHADOW_LAYERS = [
  { id: "buildings-shadow-1", translate: [3, 3] as [number, number], opacity: { light: 0.1, dark: 0.16 } },
  { id: "buildings-shadow-2", translate: [4.5, 4.5] as [number, number], opacity: { light: 0.08, dark: 0.12 } },
  { id: "buildings-shadow-3", translate: [6, 6] as [number, number], opacity: { light: 0.05, dark: 0.08 } },
] as const;

// FX-2: пороги цвета — квантили реального распределения индекса.
// Жёсткий step 5/7 после скоринга v4 давал ~56% зданий одним бежевым
// (бэклог FX №2). Линейный градиент в RGB не подошёл: смешение пастелей
// даёт выцветшие промежуточные тона. Поэтому — 5 ступеней (квинтили),
// каждая по ~20% зданий, соседние оттенки близки по тону, но насыщенные.
// Цвет — презентация: формула индекса (scoring.json) не затрагивается.
// null = данных мало, фолбэк на старый step 5/7.
type ColorStops = { t1: number; t2: number; t3: number; t4: number; mid: number };

const DEFAULT_COLOR_STOPS: ColorStops = {
  t1: 6.1,
  t2: 6.8,
  t3: 7.4,
  t4: 7.9,
  mid: 7.1,
};

const NON_HOME_DIM_MIN_ZOOM = 15.5;

const isNonHomeBuildingExpr = () => [
  "any",
  ["==", ["get", "is_home"], false],
  ["!", ["has", "index"]],
];

const stepColorsExpr = (p: Palette, s: ColorStops | null, lighten = 0) => {
  const stops = s ?? DEFAULT_COLOR_STOPS;
  const tone = (c: string) => (lighten > 0 ? mixHex(c, "#ffffff", lighten) : c);
  return [
    "step",
    ["coalesce", ["get", "index"], stops.mid],
    tone(p.vlow),
    stops.t1,
    tone(p.low),
    stops.t2,
    tone(p.mid),
    stops.t3,
    tone(p.high),
    stops.t4,
    tone(p.vhigh),
  ];
};

const selectedStepColorsExpr = (p: Palette, s: ColorStops | null, lighten = 0) => {
  const stops = s ?? DEFAULT_COLOR_STOPS;
  const tone = (c: string) => (lighten > 0 ? mixHex(c, "#ffffff", lighten) : c);
  return [
    "step",
    ["coalesce", ["get", "index"], stops.mid],
    tone(p.selectedVlow),
    stops.t1,
    tone(p.selectedLow),
    stops.t2,
    tone(p.selectedMid),
    stops.t3,
    tone(p.selectedHigh),
    stops.t4,
    tone(p.selectedVhigh),
  ];
};

const roofStepColorsExpr = (p: Palette, s: ColorStops | null) => {
  const stops = s ?? DEFAULT_COLOR_STOPS;
  return [
    "step",
    ["coalesce", ["get", "index"], stops.mid],
    p.roofVlow,
    stops.t1,
    p.roofLow,
    stops.t2,
    p.roofMid,
    stops.t3,
    p.roofHigh,
    stops.t4,
    p.roofVhigh,
  ];
};

const buildingColorAtZoomExpr = (
  p: Palette,
  s: ColorStops | null,
  selectedColor: string | null,
  otherColor: string,
) => {
  const stepExpr = stepColorsExpr(p, s, 0);
  const selectedStepExpr = selectedStepColorsExpr(p, s, 0);
  return [
    "case",
    ["to-boolean", ["feature-state", "selected"]],
    selectedColor ?? selectedStepExpr,
    isNonHomeBuildingExpr(),
    otherColor,
    stepExpr,
  ];
};

const buildingColorExpr = (
  p: Palette,
  s: ColorStops | null,
  selectedColor: string | null = null,
) =>
  [
    "step",
    ["zoom"],
    buildingColorAtZoomExpr(p, s, selectedColor, p.otherOverview),
    NON_HOME_DIM_MIN_ZOOM,
    buildingColorAtZoomExpr(p, s, selectedColor, p.other),
  ] as never;

// Крыши жилых домов — 5 ступеней V4b2a; нежилых — холодный нейтральный тон
// (с дополнительным приглушением на z >= 15.5); у выбранного — яркая ступень.
const buildingRoofColorAtZoomExpr = (
  p: Palette,
  s: ColorStops | null,
  roofOtherColor: string,
) => {
  const roofStepExpr = roofStepColorsExpr(p, s);
  const selectedRoofStepExpr = selectedStepColorsExpr(p, s, 0.05);
  return [
    "case",
    ["to-boolean", ["feature-state", "selected"]],
    selectedRoofStepExpr,
    isNonHomeBuildingExpr(),
    roofOtherColor,
    roofStepExpr,
  ];
};

const buildingRoofColorExpr = (p: Palette, s: ColorStops | null) =>
  [
    "step",
    ["zoom"],
    buildingRoofColorAtZoomExpr(p, s, p.roofOtherOverview),
    NON_HOME_DIM_MIN_ZOOM,
    buildingRoofColorAtZoomExpr(p, s, p.roofOther),
  ] as never;

// Сплошной полигон крыши, слегка расширенный наружу (~1.5 px на текущем зуме):
// рисуется на 5 см ниже основной крыши дома (buildings-selected-roof), поэтому
// вся внутренняя площадь и любые стыки тайлов перекрываются крышей цвета ступени,
// а снаружи остаётся только чистый тонкий контур по внешнему периметру крыши.
function outsetPolygonRing(ring: Position[], outsetMeters: number): Position[] | null {
  const pts: [number, number][] = [];
  for (const pt of ring) {
    const lon = pt[0];
    const lat = pt[1];
    if (typeof lon !== "number" || typeof lat !== "number") continue;
    const prev = pts[pts.length - 1];
    if (!prev || Math.hypot(lon - prev[0], lat - prev[1]) > 1e-7) {
      pts.push([lon, lat]);
    }
  }
  if (pts.length > 1) {
    const first = pts[0];
    const last = pts[pts.length - 1];
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) < 1e-7) {
      pts.pop();
    }
  }
  const n = pts.length;
  if (n < 3) return null;

  const lon0 = pts[0][0];
  const lat0 = pts[0][1];
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const ky = 110574;
  const local: [number, number][] = pts.map(([lon, lat]) => [
    (lon - lon0) * kx,
    (lat - lat0) * ky,
  ]);

  let signedArea2 = 0;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = local[i];
    const [x2, y2] = local[(i + 1) % n];
    signedArea2 += x1 * y2 - x2 * y1;
  }
  if (Math.abs(signedArea2) * 0.5 < 2) return null;

  const d = outsetMeters;
  // Внешняя нормаль противоположна внутренней:
  const sign = signedArea2 >= 0 ? -1 : 1;

  const edgeNormals: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const [x1, y1] = local[i];
    const [x2, y2] = local[(i + 1) % n];
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    edgeNormals.push([(-sign * dy) / len, (sign * dx) / len]);
  }

  const outer: Position[] = [];
  for (let i = 0; i < n; i++) {
    const n1 = edgeNormals[(i - 1 + n) % n];
    const n2 = edgeNormals[i];
    const dot = n1[0] * n2[0] + n1[1] * n2[1];
    const denom = Math.max(0.35, 1 + dot);
    const k = Math.min(d * 1.35, d / denom);
    const ox = local[i][0] + k * (n1[0] + n2[0]);
    const oy = local[i][1] + k * (n1[1] + n2[1]);
    outer.push([lon0 + ox / kx, lat0 + oy / ky]);
  }
  outer.push(outer[0]);
  return outer;
}

function buildRoofEdgeFeatures(
  features: maplibregl.GeoJSONFeature[],
  zoom: number,
): Feature[] {
  // ~1.5 px в метрах на текущем зуме (на широте Москвы 55.756°).
  const metersPerPx = (156543.03 * Math.cos((55.756 * Math.PI) / 180)) / Math.pow(2, zoom);
  const outsetMeters = Math.max(0.26, Math.min(1.8, metersPerPx * 1.15));
  const out: Feature[] = [];
  const seen = new Set<string>();

  const pushPolygon = (
    outerRing: Position[],
    id: string | number | undefined,
    height: number,
  ) => {
    if (!outerRing || outerRing.length < 4) return;
    const p0 = outerRing[0];
    const key = `${outerRing.length}:${p0[0]?.toFixed(5)},${p0[1]?.toFixed(5)}`;
    if (seen.has(key)) return;
    seen.add(key);
    const expandedRing = outsetPolygonRing(outerRing, outsetMeters);
    if (!expandedRing) return;
    out.push({
      type: "Feature",
      id,
      properties: { id, height },
      geometry: {
        type: "Polygon",
        coordinates: [expandedRing],
      },
    });
  };

  for (const f of features) {
    const geom = f.geometry;
    const id = f.id ?? f.properties?.id;
    const height = Number(f.properties?.height ?? 9);
    if (geom.type === "Polygon") {
      pushPolygon(geom.coordinates[0], id, height);
    } else if (geom.type === "MultiPolygon") {
      for (const poly of geom.coordinates) {
        pushPolygon(poly[0], id, height);
      }
    }
  }
  return out;
}

// ── V4b4: Hero-окна выбранного дома (настоящие 3D-квады fill-extrusion по фасадам) ──
const HERO_WIN_MIN_EDGE_M = 3.0;
const HERO_WIN_CORNER_MARGIN_M = 0.2;
const HERO_WIN_STEP_MIN_M = 2.4;
const HERO_WIN_STEP_MAX_M = 3.0;
const HERO_WIN_STEP_DEFAULT_M = 2.68;
const HERO_FLOOR_FIRST_M = 1.0;
const HERO_FLOOR_STEP_M = 3.0;
const HERO_MAX_FLOORS = 30;
const HERO_MAX_TOTAL_POLYGONS = 2000;
const HERO_MAX_WINDOW_CELLS = 1200;
const HERO_HALO_SCALE = 1.2;

const HERO_WINDOW_LAYER_ID = "buildings-selected-windows";
const HERO_HALO_LAYER_ID = "buildings-selected-windows-halo";
const HERO_WINDOWS_SOURCE_ID = "selectedWindows";

// V4b5: санитарный порог площади. Если после сборки частей контур вышел
// меньше ~150 м² у дома в 5+ этажей — это почти наверняка обрезок тайла:
// окна на нём не строим (лучше без окон, чем окна на невидимой грани).
const HERO_MIN_FOOTPRINT_AREA_M2 = 150;
const HERO_MIN_FLOORS_FOR_AREA_CHECK = 5;

type LonLatPair = [number, number];

function cleanLonLatRing(ring: Position[]): LonLatPair[] {
  const pts: LonLatPair[] = [];
  for (const pt of ring) {
    const lon = pt[0];
    const lat = pt[1];
    if (typeof lon !== "number" || typeof lat !== "number") continue;
    const prev = pts[pts.length - 1];
    if (!prev || Math.hypot(lon - prev[0], lat - prev[1]) > 1e-9) {
      pts.push([lon, lat]);
    }
  }
  if (pts.length > 1) {
    const first = pts[0];
    const last = pts[pts.length - 1];
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) < 1e-9) {
      pts.pop();
    }
  }
  if (pts.length >= 3) pts.push(pts[0]);
  return pts;
}

function ringAreaM2(ring: LonLatPair[]): number {
  if (ring.length < 4) return 0;
  const [lon0, lat0] = ring[0];
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const ky = 110574;
  let area2 = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[i + 1];
    const x1 = (lon1 - lon0) * kx;
    const y1 = (lat1 - lat0) * ky;
    const x2 = (lon2 - lon0) * kx;
    const y2 = (lat2 - lat0) * ky;
    area2 += x1 * y2 - x2 * y1;
  }
  return Math.abs(area2) * 0.5;
}

function footprintAreaM2(geom: Geometry): number {
  const polygonArea = (rings: Position[][]): number => {
    let area = 0;
    for (let i = 0; i < rings.length; i++) {
      const a = ringAreaM2(cleanLonLatRing(rings[i]));
      area += i === 0 ? a : -a;
    }
    return Math.max(0, area);
  };
  if (geom.type === "Polygon") return polygonArea(geom.coordinates);
  if (geom.type === "MultiPolygon") {
    return geom.coordinates.reduce((acc, poly) => acc + polygonArea(poly), 0);
  }
  return 0;
}

function geometrySignature(geom: Geometry): string {
  const ringKey = (ring: Position[]) =>
    ring.map(([lon, lat]) => `${lon.toFixed(6)},${lat.toFixed(6)}`).join(";");
  if (geom.type === "Polygon") {
    return `P:${geom.coordinates.map(ringKey).join("|")}`;
  }
  if (geom.type === "MultiPolygon") {
    return `M:${geom.coordinates.map((poly) => poly.map(ringKey).join("/")).join("|")}`;
  }
  return "";
}

// V4b5: если смотреть на длинный дом с ребра, окна на фасаде не видны.
// Для выбранного дома выбираем bearing так, чтобы камера смотрела на самый
// длинный южный фасад (внешнее кольцо, без дворов).
function facadeCameraBearing(geom: Geometry): number | null {
  const norm = (deg: number) => ((((deg + 180) % 360) + 360) % 360) - 180;
  let bestLen = 0;
  let bestNormals: [number, number] | null = null;
  const visitRing = (ring: Position[]) => {
    for (let i = 0; i < ring.length - 1; i++) {
      const [lon1, lat1] = ring[i];
      const [lon2, lat2] = ring[i + 1];
      if (
        typeof lon1 !== "number" ||
        typeof lat1 !== "number" ||
        typeof lon2 !== "number" ||
        typeof lat2 !== "number"
      ) {
        continue;
      }
      const kx = 111320 * Math.cos((lat1 * Math.PI) / 180);
      const dx = (lon2 - lon1) * kx;
      const dy = (lat2 - lat1) * 110574;
      const len = Math.hypot(dx, dy);
      if (len < 6 || len <= bestLen) continue;
      const azimuth = (Math.atan2(dx, dy) * 180) / Math.PI;
      bestLen = len;
      bestNormals = [azimuth + 90, azimuth - 90];
    }
  };
  if (geom.type === "Polygon") {
    visitRing(geom.coordinates[0]);
  } else if (geom.type === "MultiPolygon") {
    for (const poly of geom.coordinates) visitRing(poly[0]);
  }
  const normals = bestNormals as [number, number] | null;
  if (normals === null) return null;
  const [n1, n2] = normals;
  // Из двух сторон длинного фасада берём ту, что ближе к югу: камера
  // остаётся в привычной южной полусфере.
  const normal =
    Math.abs(norm(n1 - 180)) <= Math.abs(norm(n2 - 180)) ? n1 : n2;
  return norm(normal - 180);
}

// V4b5: дом режется границами тайлов PMTiles — склеиваем все его части
// (union) в один footprint. Порядок колец и дыры сохраняет polygon-clipping:
// внешние кольца CCW, внутренние (дворы) CW.
function unionFootprintGeometries(geoms: Geometry[]): Geometry | null {
  if (geoms.length === 1 && geoms[0].type === "Polygon") {
    const rings = geoms[0].coordinates
      .map(cleanLonLatRing)
      .filter((ring) => ring.length >= 4);
    return rings.length ? { type: "Polygon", coordinates: rings } : null;
  }

  const polygons: LonLatPair[][][] = [];
  for (const geom of geoms) {
    if (geom.type === "Polygon") {
      const rings = geom.coordinates.map(cleanLonLatRing).filter((r) => r.length >= 4);
      if (rings.length) polygons.push(rings);
    } else if (geom.type === "MultiPolygon") {
      for (const poly of geom.coordinates) {
        const rings = poly.map(cleanLonLatRing).filter((r) => r.length >= 4);
        if (rings.length) polygons.push(rings);
      }
    }
  }
  const valid = polygons.filter((poly) => poly.length > 0);
  if (!valid.length) return null;
  if (valid.length === 1) {
    return { type: "Polygon", coordinates: valid[0] as unknown as Position[][] };
  }
  try {
    const merged = polygonClipping.union(
      valid[0] as never,
      ...(valid.slice(1) as never[]),
    ) as unknown as LonLatPair[][][];
    const polys = merged.filter((poly) => poly.length > 0);
    if (!polys.length) return null;
    return { type: "MultiPolygon", coordinates: polys as unknown as Position[][][] };
  } catch (e) {
    // Булева операция не должна ронять карту: показываем части как есть,
    // санитарная проверка площади ниже не даст строить окна на обрезке.
    console.warn("Не удалось объединить части контура дома:", e);
    return { type: "MultiPolygon", coordinates: valid as unknown as Position[][][] };
  }
}

type HeroFacadeEdge = {
  lon0: number;
  lat0: number;
  kx: number;
  ky: number;
  x1: number;
  y1: number;
  tx: number;
  ty: number;
  nx: number;
  ny: number;
  len: number;
  floors: number;
  buildingHeight: number;
  buildingId: number;
};

function resolveSelectedBodyColor(
  theme: Theme,
  indexVal: number | null | undefined,
  s: ColorStops | null,
): string {
  const p = MAP_THEMES[theme];
  if (theme === "dark") return p.selectedBody;
  const stops = s ?? DEFAULT_COLOR_STOPS;
  const idx =
    typeof indexVal === "number" && Number.isFinite(indexVal) ? indexVal : stops.mid;
  if (idx < stops.t1) return p.selectedVlow;
  if (idx < stops.t2) return p.selectedLow;
  if (idx < stops.t3) return p.selectedMid;
  if (idx < stops.t4) return p.selectedHigh;
  return p.selectedVhigh;
}

function buildQuadRing(
  edge: HeroFacadeEdge,
  s0: number,
  s1: number,
  dInner: number,
  dOuter: number,
): Position[] {
  const { lon0, lat0, kx, ky, x1, y1, tx, ty, nx, ny } = edge;
  const pt = (s: number, d: number): Position => [
    lon0 + (x1 + s * tx + d * nx) / kx,
    lat0 + (y1 + s * ty + d * ny) / ky,
  ];
  const p00 = pt(s0, dInner);
  const p10 = pt(s1, dInner);
  const p11 = pt(s1, dOuter);
  const p01 = pt(s0, dOuter);
  const cross = tx * ny - ty * nx;
  return cross < 0 ? [p00, p10, p11, p01, p00] : [p00, p01, p11, p10, p00];
}

function buildSelectedWindowFeatures(
  features: maplibregl.GeoJSONFeature[],
  colorStops: ColorStops | null,
  includeHalo: boolean,
): FeatureCollection {
  const edges: HeroFacadeEdge[] = [];
  const bodyOccluders: Feature[] = [];
  const seenRings = new Set<string>();

  const collectRingEdges = (
    ring: Position[],
    isHole: boolean,
    floors: number,
    buildingHeight: number,
    buildingId: number,
  ) => {
    const pts: [number, number][] = [];
    for (const pt of ring) {
      const lon = pt[0];
      const lat = pt[1];
      if (typeof lon !== "number" || typeof lat !== "number") continue;
      const prev = pts[pts.length - 1];
      if (!prev || Math.hypot(lon - prev[0], lat - prev[1]) > 1e-7) {
        pts.push([lon, lat]);
      }
    }
    if (pts.length > 1) {
      const first = pts[0];
      const last = pts[pts.length - 1];
      if (Math.hypot(first[0] - last[0], first[1] - last[1]) < 1e-7) {
        pts.pop();
      }
    }
    const n = pts.length;
    if (n < 3) return;

    const ringKey = `${isHole ? "H" : "O"}:${n}:${pts[0][0].toFixed(5)},${pts[0][1].toFixed(5)}`;
    if (seenRings.has(ringKey)) return;
    seenRings.add(ringKey);

    const lon0 = pts[0][0];
    const lat0 = pts[0][1];
    const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
    const ky = 110574;
    const local: [number, number][] = pts.map(([lon, lat]) => [
      (lon - lon0) * kx,
      (lat - lat0) * ky,
    ]);

    let signedArea2 = 0;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = local[i];
      const [x2, y2] = local[(i + 1) % n];
      signedArea2 += x1 * y2 - x2 * y1;
    }
    if (Math.abs(signedArea2) * 0.5 < 1.5) return;

    // Нормаль наружу от стены здания: для внешнего контура — наружу полигона,
    // для внутреннего двора (hole) — внутрь двора (наружу от стены дома).
    const baseSign = signedArea2 >= 0 ? -1 : 1;
    const sign = isHole ? -baseSign : baseSign;

    for (let i = 0; i < n; i++) {
      const [x1, y1] = local[i];
      const [x2, y2] = local[(i + 1) % n];
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy);
      if (len < HERO_WIN_MIN_EDGE_M) continue;
      edges.push({
        lon0,
        lat0,
        kx,
        ky,
        x1,
        y1,
        tx: dx / len,
        ty: dy / len,
        nx: (-sign * dy) / len,
        ny: (sign * dx) / len,
        len,
        floors,
        buildingHeight,
        buildingId,
      });
    }
  };

  for (const f of features) {
    const rawId = f.id ?? f.properties?.id ?? 1;
    const buildingId =
      typeof rawId === "number" && Number.isFinite(rawId) ? rawId : Number(rawId) || 1;
    const buildingHeight = Math.max(0, Number(f.properties?.height ?? 9));
    const indexVal =
      typeof f.properties?.index === "number" ? f.properties.index : Number(f.properties?.index);
    const bodyColorLight = resolveSelectedBodyColor("light", indexVal, colorStops);
    const bodyColorDark = resolveSelectedBodyColor("dark", indexVal, colorStops);

    const insetGeometry = (geom: Geometry): Geometry => {
      if (geom.type === "Polygon") {
        const outer = outsetPolygonRing(geom.coordinates[0], -0.12) ?? geom.coordinates[0];
        return { type: "Polygon", coordinates: [outer, ...geom.coordinates.slice(1)] };
      }
      if (geom.type === "MultiPolygon") {
        return {
          type: "MultiPolygon",
          coordinates: geom.coordinates.map((poly) => {
            const outer = outsetPolygonRing(poly[0], -0.12) ?? poly[0];
            return [outer, ...poly.slice(1)];
          }),
        };
      }
      return geom;
    };

    bodyOccluders.push({
      type: "Feature",
      properties: {
        kind: "body",
        base: 0,
        height: buildingHeight,
        colorLight: bodyColorLight,
        colorDark: bodyColorDark,
      },
      geometry: insetGeometry(f.geometry),
    });

    const floors = Math.min(
      HERO_MAX_FLOORS,
      Math.max(0, Math.floor((buildingHeight - HERO_FLOOR_FIRST_M) / HERO_FLOOR_STEP_M)),
    );
    if (floors <= 0) continue;

    const footprintArea = footprintAreaM2(f.geometry);
    if (
      footprintArea < HERO_MIN_FOOTPRINT_AREA_M2 &&
      floors >= HERO_MIN_FLOORS_FOR_AREA_CHECK
    ) {
      console.warn(
        `Hero-окна: контур дома ${buildingId} подозрительно мал ` +
          `(${footprintArea.toFixed(0)} м² при ${floors} этажах) — окна не строим.`,
      );
      continue;
    }

    const geom = f.geometry;
    if (geom.type === "Polygon") {
      for (let rIdx = 0; rIdx < geom.coordinates.length; rIdx++) {
        collectRingEdges(geom.coordinates[rIdx], rIdx > 0, floors, buildingHeight, buildingId);
      }
    } else if (geom.type === "MultiPolygon") {
      for (const poly of geom.coordinates) {
        for (let rIdx = 0; rIdx < poly.length; rIdx++) {
          collectRingEdges(poly[rIdx], rIdx > 0, floors, buildingHeight, buildingId);
        }
      }
    }
  }

  if (!edges.length) {
    return { type: "FeatureCollection", features: [] };
  }

  const colsForStep = (len: number, targetStep: number, minStep: number): number => {
    const usable = Math.max(0, len - 2 * HERO_WIN_CORNER_MARGIN_M);
    if (usable < HERO_WIN_MIN_EDGE_M - 2 * HERO_WIN_CORNER_MARGIN_M) return 0;
    const maxCols = Math.max(1, Math.floor(usable / minStep));
    const est = Math.max(1, Math.round(usable / targetStep));
    return Math.min(maxCols, est);
  };

  let targetStep = HERO_WIN_STEP_DEFAULT_M;
  let minStep = HERO_WIN_STEP_MIN_M;
  let maxStep = HERO_WIN_STEP_MAX_M;

  const countTotalCells = (step: number, mStep: number) =>
    edges.reduce((acc, e) => acc + colsForStep(e.len, step, mStep) * e.floors, 0);

  let totalCells = countTotalCells(targetStep, minStep);
  if (totalCells > HERO_MAX_WINDOW_CELLS) {
    const scale = totalCells / HERO_MAX_WINDOW_CELLS;
    targetStep *= scale;
    minStep *= scale;
    maxStep *= scale;
    while (countTotalCells(targetStep, minStep) > HERO_MAX_WINDOW_CELLS) {
      targetStep *= 1.08;
      minStep *= 1.08;
      maxStep *= 1.08;
    }
  }

  let seed = ((edges[0].buildingId * 2654435761) >>> 0) || 1337;
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };

  type RawWinSpec = {
    edge: HeroFacadeEdge;
    sCenter: number;
    winWidth: number;
    winHeight: number;
    winBase: number;
    winTop: number;
    score: number;
    toneT: number;
    aptToneT: number;
  };

  const specs: RawWinSpec[] = [];
  const widthChoices = [1.26, 1.38, 1.48, 1.56] as const;

  for (const edge of edges) {
    const cols = colsForStep(edge.len, targetStep, minStep);
    if (cols <= 0) continue;
    const usable = edge.len - 2 * HERO_WIN_CORNER_MARGIN_M;
    const actualStep = Math.min(maxStep, Math.max(minStep, usable / cols));
    const totalSpan = cols * actualStep;
    const startOffset = (edge.len - totalSpan) * 0.5;

    // Разбиваем колонки фасада на квартиры по 2–3 соседних окна (кластерный рисунок).
    const colAptGroup: number[] = [];
    const colWidths: number[] = [];
    let aptId = 0;
    let inApt = 0;
    let aptLen = rnd() < 0.52 ? 2 : 3;
    for (let c = 0; c < cols; c++) {
      if (inApt >= aptLen) {
        aptId++;
        inApt = 0;
        aptLen = rnd() < 0.52 ? 2 : 3;
      }
      inApt++;
      colAptGroup.push(aptId);
      const rawW = widthChoices[(c + Math.floor(rnd() * 2)) % widthChoices.length];
      colWidths.push(Math.min(1.6, Math.max(1.2, Math.min(rawW, actualStep - 0.55))));
    }
    const numApts = aptId + 1;
    const aptVertBias = Array.from({ length: numApts }, () => (rnd() - 0.5) * 0.22);

    for (let r = 0; r < edge.floors; r++) {
      const winHeight = r === 0 ? 1.68 : 1.74;
      const winBase = HERO_FLOOR_FIRST_M + r * HERO_FLOOR_STEP_M + 0.16;
      const winTop = Math.min(edge.buildingHeight - 0.15, winBase + winHeight);
      if (winTop - winBase < 0.8) continue;

      const aptFloorScore: number[] = [];
      const aptFloorTone: number[] = [];
      for (let a = 0; a < numApts; a++) {
        aptFloorScore.push(rnd() * 0.88 + aptVertBias[a]);
        aptFloorTone.push(rnd());
      }

      for (let c = 0; c < cols; c++) {
        const sCenter = startOffset + (c + 0.5) * actualStep;
        const g = colAptGroup[c];
        // Сильная кластеризация по квартире (2–3 соседних окна горят или гаснут вместе).
        const score = aptFloorScore[g] + (rnd() - 0.5) * 0.14;
        specs.push({
          edge,
          sCenter,
          winWidth: colWidths[c],
          winHeight: winTop - winBase,
          winBase,
          winTop,
          score,
          toneT: rnd(),
          aptToneT: aptFloorTone[g],
        });
      }
    }
  }

  if (!specs.length) {
    return { type: "FeatureCollection", features: [] };
  }

  // Ночью строго 45–55% горящих окон (цель 50%).
  const targetLitRatio = 0.5;
  const sortedScores = specs.map((s) => s.score).sort((a, b) => a - b);
  const thresholdIdx = Math.min(
    sortedScores.length - 1,
    Math.max(0, Math.floor(sortedScores.length * (1 - targetLitRatio))),
  );
  const litThreshold = sortedScores[thresholdIdx];

  const outFeatures: Feature[] = [...bodyOccluders];

  for (const sp of specs) {
    const isLit = sp.score >= litThreshold;
    const blendT = Math.min(1, Math.max(0, sp.aptToneT * 0.65 + sp.toneT * 0.35));

    const winColorLight = mixHex("#d4b795", "#e3c7a5", blendT);
    const winColorDark = isLit
      ? mixHex("#ffc978", "#ffd79a", blendT)
      : mixHex("#16263c", "#1b2e4a", sp.toneT);

    const halfW = sp.winWidth * 0.5;
    const s0 = Math.max(HERO_WIN_CORNER_MARGIN_M, sp.sCenter - halfW);
    const s1 = Math.min(sp.edge.len - HERO_WIN_CORNER_MARGIN_M, sp.sCenter + halfW);
    if (s1 - s0 < 0.4) continue;

    outFeatures.push({
      type: "Feature",
      properties: {
        kind: "window",
        base: Number(sp.winBase.toFixed(2)),
        height: Number(sp.winTop.toFixed(2)),
        colorLight: winColorLight,
        colorDark: winColorDark,
        lit: isLit,
      },
      geometry: {
        type: "Polygon",
        coordinates: [buildQuadRing(sp.edge, s0, s1, 0.025, 0.085)],
      },
    });

    if (includeHalo && isLit && outFeatures.length < HERO_MAX_TOTAL_POLYGONS) {
      const haloHalfW = sp.winWidth * HERO_HALO_SCALE * 0.5;
      const hs0 = Math.max(HERO_WIN_CORNER_MARGIN_M * 0.75, sp.sCenter - haloHalfW);
      const hs1 = Math.min(
        sp.edge.len - HERO_WIN_CORNER_MARGIN_M * 0.75,
        sp.sCenter + haloHalfW,
      );
      const zMid = (sp.winBase + sp.winTop) * 0.5;
      const haloHalfH = sp.winHeight * HERO_HALO_SCALE * 0.5;
      const haloBase = Math.max(HERO_FLOOR_FIRST_M * 0.9, zMid - haloHalfH);
      const haloTop = Math.min(sp.edge.buildingHeight - 0.06, zMid + haloHalfH);

      outFeatures.push({
        type: "Feature",
        properties: {
          kind: "halo",
          base: Number(haloBase.toFixed(2)),
          height: Number(haloTop.toFixed(2)),
          colorLight: winColorLight,
          colorDark: winColorDark,
          lit: true,
        },
        geometry: {
          type: "Polygon",
          coordinates: [buildQuadRing(sp.edge, hs0, hs1, 0.015, 0.075)],
        },
      });
    }
  }

  return {
    type: "FeatureCollection",
    features: outFeatures,
  };
}

// ── Окна на фасадах: pattern-текстуры, сгенерированные в runtime ─────────
// V4b3: спокойные фасады без оконной сетки-решётки:
// - Обычные дома днём: чистая плоская база цвета ступени (только вертикальный градиент MapLibre).
// - Обычные дома ночью: тёмно-синяя база ступени + только редкие горящие окна (5–8%)
//   тёплым янтарём (#ffc978…#ffd79a), мелкие (2–5 px), без негорящей решётки и швов.
// - Выбранный дом: в светлой теме — кремовые окна светлее фасада без тёмной сетки,
//   в тёмной теме — тёплые янтарные окна без синей решётки негорящих рам.
const FACADE_SIZE = 384;
const FACADE_FLAT_SIZE = 16;
const FACADE_COARSE_MIN_ZOOM = 15.4;
const FACADE_MID_MIN_ZOOM = 16.0;
const FACADE_FINE_MIN_ZOOM = 17;
const FACADE_SELECT_ZOOM = 16.6;
// V4b6: неоновое свечение у основания выбранного дома (только тёмная тема).
// Три обводки по footprint на земле + яркая кромка + подсветка низа стен.
// Ширина и прозрачность интерполируются по зуму, чтобы свечение не «худело»
// при приближении: на 15.4 — ×0.8, на 18 — ×1.3.
const GLOW_GROUND_COLOR = "#2ee6a0";
const GLOW_EDGE_COLOR = "#7dffcf";
const GLOW_RINGS = [
  { id: "glow-ground-1", width: 14, blur: 8, opacity: 0.34 },
  { id: "glow-ground-2", width: 34, blur: 20, opacity: 0.2 },
  { id: "glow-ground-3", width: 72, blur: 40, opacity: 0.1 },
] as const;
const GLOW_BASE_LAYER_ID = "glow-base-walls";
const GLOW_EDGE_LAYER_ID = "glow-ground-edge";

function glowZoomScale(base: number): unknown[] {
  return [
    "interpolate",
    ["linear"],
    ["zoom"],
    FACADE_COARSE_MIN_ZOOM,
    base * 0.8,
    FACADE_SELECT_ZOOM,
    base,
    18,
    base * 1.3,
  ];
}

// В светлой теме свечение едва заметно (максимум ~0.08 суммарно).
function glowRingOpacity(base: number, theme: Theme): unknown[] {
  const o = theme === "dark" ? base : base * 0.18;
  return glowZoomScale(o);
}

const FACADE_STEPS = ["vlow", "low", "mid", "high", "vhigh"] as const;
const FACADE_SELECTED_STEPS = [
  "selected-vlow",
  "selected-low",
  "selected-mid",
  "selected-high",
  "selected-vhigh",
] as const;
type FacadeStep = (typeof FACADE_STEPS)[number];
type FacadeSelectedStep = (typeof FACADE_SELECTED_STEPS)[number];
type FacadeVariant =
  | FacadeStep
  | "dim"
  | "other"
  | "other-overview"
  | "selected"
  | FacadeSelectedStep;
type FacadeLod = "flat" | "subtle" | "coarse" | "fine";

function facadeLodForZoom(zoom: number): FacadeLod {
  if (zoom < FACADE_COARSE_MIN_ZOOM) return "flat";
  if (zoom < FACADE_MID_MIN_ZOOM) return "subtle";
  if (zoom < FACADE_FINE_MIN_ZOOM) return "coarse";
  return "fine";
}

function facadePixelRatio(lod: FacadeLod): number {
  if (lod === "flat") return 1;
  if (lod === "subtle") return 1.35;
  if (lod === "coarse") return 1.35;
  return 1.75;
}

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function mixHex(a: string, b: string, t: number): string {
  const [ar, ag, ab] = parseHex(a);
  const [br, bg, bb] = parseHex(b);
  const mix = (x: number, y: number) =>
    Math.round(x + (y - x) * t)
      .toString(16)
      .padStart(2, "0");
  return `#${mix(ar, br)}${mix(ag, bg)}${mix(ab, bb)}`;
}

// Дневное стекло выбранного дома — тёплый крем (#f6e7cc…#ffe9c9),
// ночное горящее — тёплый янтарь (#ffc978…#ffd79a).
const DAY_CREAM_GLASS = ["#f6e7cc", "#f9e8ce", "#fce9cc", "#ffe9c9"] as const;
const NIGHT_AMBER_GLASS = ["#ffc978", "#ffce82", "#ffd38e", "#ffd79a"] as const;

type WindowKind = "narrow" | "single" | "wide" | "balcony";
type FacadeCol = { x: number; kind: WindowKind; jitterX: number; aptGroup: number };

function makeFacadeImage(
  base: string,
  theme: Theme,
  variant: FacadeVariant,
  lod: FacadeLod,
): ImageData {
  const isSelected = variant === "selected" || variant.startsWith("selected-");
  const isNonHome = variant === "other" || variant === "other-overview";
  // Обычные дома в светлой теме на всех зумах, нежилые здания в обеих темах
  // и любые дома при zoom < 15.4 ("flat") — чистая плоская база без швов и окон.
  const isFlatBase = lod === "flat" || isNonHome || (theme === "light" && !isSelected);
  const size = isFlatBase ? FACADE_FLAT_SIZE : FACADE_SIZE;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d недоступен");
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, size, size);
  if (isFlatBase) return ctx.getImageData(0, 0, size, size);

  // Детерминированный генератор — свой рисунок огней для каждого варианта.
  let seed =
    (theme === "dark" ? 19 : 7) +
    (lod === "subtle" ? 37 : lod === "coarse" ? 73 : 127) +
    variant.charCodeAt(0) * 197 +
    (variant.length > 2 ? variant.charCodeAt(1) * 89 : 0) +
    (variant.length > 3 ? variant.charCodeAt(variant.length - 1) * 41 : 0);
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };

  const isFine = lod === "fine";
  const isSubtle = lod === "subtle";

  // ── Ветка 1: Обычные дома ночью (база + только редкие тёплые огни 5–8%) ──
  // Никаких швов и негорящих окон: только мелкие разбросанные огни (2–5 px),
  // не более ~12 на дом в кадре.
  if (!isSelected) {
    const rowH = isFine ? 18 : 16;
    const rows = Math.floor(size / rowH);
    const numCols = 16;
    const colStep = size / numCols;

    type NightDot = {
      wx: number;
      wy: number;
      ww: number;
      wh: number;
      score: number;
      toneIdx: number;
    };
    const dots: NightDot[] = [];
    const colBias = Array.from({ length: numCols }, () => (rnd() - 0.5) * 0.24);

    for (let r = 0; r < rows; r++) {
      const rowBoost = rnd() < 0.25 ? 0.18 : rnd() < 0.6 ? 0 : -0.15;
      for (let c = 0; c < numCols; c++) {
        const jx = Math.round((rnd() - 0.5) * 4);
        const jy = Math.round((rnd() - 0.5) * 2);
        const vr = rnd();
        const ww = isSubtle
          ? 3
          : isFine
            ? vr > 0.5
              ? 4
              : 3
            : vr > 0.45
              ? 4
              : 3;
        const wh = isSubtle
          ? 3
          : isFine
            ? vr > 0.45
              ? 5
              : 4
            : 4;
        const wx = Math.max(2, Math.min(size - ww - 2, Math.round(c * colStep + colStep * 0.35 + jx)));
        const wy = Math.max(2, Math.min(size - wh - 2, r * rowH + 4 + jy));
        const score = rnd() * 0.72 + rowBoost + colBias[c];
        dots.push({
          wx,
          wy,
          ww,
          wh,
          score,
          toneIdx: Math.floor(rnd() * 4),
        });
      }
    }

    // Строго 5–8% горящих окон: subtle 5.5% (единичные огни), остальные 7.0%.
    const targetLitRatio = isSubtle ? 0.055 : 0.07;
    const sorted = dots.map((d) => d.score).sort((a, b) => a - b);
    const idx = Math.min(
      sorted.length - 1,
      Math.max(0, Math.floor(sorted.length * (1 - targetLitRatio))),
    );
    const litThreshold = sorted[idx];
    const maxScore = sorted[sorted.length - 1];
    const span = Math.max(0.001, maxScore - litThreshold);

    for (const dot of dots) {
      if (dot.score < litThreshold) continue;
      const u = Math.min(1, Math.max(0, (dot.score - litThreshold) / span));
      const amber = NIGHT_AMBER_GLASS[dot.toneIdx];
      // На 15.4–16.0 (subtle) контраст строго ≤ 6%; на 16+ — тёплые янтарные огни.
      const litT = isSubtle
        ? 0.038 + u * 0.021 // max 0.059 (≤ 6%)
        : u > 0.45
          ? 0.82 + (u - 0.45) * 0.28
          : 0.58 + u * 0.48;
      ctx.fillStyle = mixHex(base, amber, Math.min(0.96, litT));
      ctx.fillRect(dot.wx, dot.wy, dot.ww, dot.wh);

      if (!isSubtle && u > 0.4) {
        ctx.fillStyle = mixHex(base, "#ffd79a", Math.min(0.98, litT + 0.1));
        ctx.fillRect(dot.wx, dot.wy, dot.ww, 1);
      }
    }

    return ctx.getImageData(0, 0, size, size);
  }

  // ── Ветка 2: Выбранный дом (день — кремовые окна без тёмной сетки; ночь — тёплые окна) ──
  // Никаких тёмных швов и внешних рамок вокруг каждого окна, чтобы фасад
  // не превращался в решётку или вафлю.
  const rowH = isFine ? 16 : 13;
  const rows = Math.floor(size / rowH);
  const sectionWidths = [96, 94, 98, 96];
  const rot = variant.charCodeAt(variant.length - 1) % sectionWidths.length;
  const orderedSections = [
    ...sectionWidths.slice(rot),
    ...sectionWidths.slice(0, rot),
  ];

  const cols: FacadeCol[] = [];
  let secStart = 0;
  let aptCounter = 0;

  for (let si = 0; si < orderedSections.length; si++) {
    const secW = orderedSections[si];
    const kinds: WindowKind[] =
      (si + rot) % 2 === 0
        ? ["single", "wide", "single", "balcony", "single"]
        : ["narrow", "single", "balcony", "single", "wide"];
    const count = kinds.length;
    const margin = 7;
    const usable = secW - margin * 2;
    const step = usable / count;

    let inApt = 0;
    let aptSize = 2 + Math.floor(rnd() * 2);
    for (let ci = 0; ci < count; ci++) {
      if (inApt >= aptSize) {
        aptCounter++;
        inApt = 0;
        aptSize = 2 + Math.floor(rnd() * 2);
      }
      inApt++;
      const jitterX = rnd() < 0.3 ? -1 : rnd() < 0.7 ? 0 : 1;
      const center = secStart + margin + Math.round(ci * step + step * 0.25);
      cols.push({
        x: center,
        kind: kinds[ci],
        jitterX,
        aptGroup: aptCounter,
      });
    }
    aptCounter++;
    secStart += secW;
  }

  type WinCell = {
    wx: number;
    wy: number;
    ww: number;
    wh: number;
    score: number;
    toneIdx: number;
    varR: number;
  };
  const cells: WinCell[] = [];
  const colBias = cols.map(() => (rnd() - 0.5) * 0.22);

  for (let r = 0; r < rows; r++) {
    const aptBoosts: number[] = [];
    for (let a = 0; a <= aptCounter; a++) {
      const ar = rnd();
      aptBoosts[a] = ar < 0.36 ? 0.42 : ar < 0.7 ? 0.16 : -0.22;
    }

    for (let c = 0; c < cols.length; c++) {
      const col = cols[c];
      if (col.kind === "narrow" && (r + c * 3) % 11 === 0 && rnd() < 0.5) {
        continue;
      }
      const ww = isFine
        ? col.kind === "wide" || col.kind === "balcony"
          ? 5
          : 4
        : col.kind === "wide" || col.kind === "balcony"
          ? 4
          : 3;
      const wh = isFine ? 6 : 4;
      const wx = Math.max(2, Math.min(size - ww - 2, col.x + col.jitterX));
      const wy = r * rowH + (isFine ? 4 : 3);
      const score =
        rnd() * 0.65 +
        (aptBoosts[col.aptGroup] ?? 0) +
        colBias[c] +
        (col.kind === "narrow" ? -0.08 : 0);

      cells.push({
        wx,
        wy,
        ww,
        wh,
        score,
        toneIdx: Math.floor(rnd() * 4),
        varR: rnd(),
      });
    }
  }

  if (theme === "light") {
    // Светлая тема, выбранный дом: кремовые окна светлее фасада,
    // без тёмной сетки, без тёмных швов и без тёмных подоконников.
    for (const cell of cells) {
      const { wx, wy, ww, wh, toneIdx, varR } = cell;
      const cream = DAY_CREAM_GLASS[toneIdx];
      const glassT = isSubtle ? 0.14 + varR * 0.06 : 0.30 + varR * 0.12;
      ctx.fillStyle = mixHex(base, cream, glassT);
      ctx.fillRect(wx, wy, ww, wh);
      if (isFine) {
        ctx.fillStyle = mixHex(base, "#ffe9c9", Math.min(0.48, glassT + 0.1));
        ctx.fillRect(wx, wy, ww, 1);
      }
    }
    return ctx.getImageData(0, 0, size, size);
  }

  // Тёмная тема, выбранный дом: ~48% горящих янтарных окон, без синей решётки.
  const targetLitRatio = 0.48;
  const sorted = cells.map((cell) => cell.score).sort((a, b) => a - b);
  const idx = Math.min(
    sorted.length - 1,
    Math.max(0, Math.floor(sorted.length * (1 - targetLitRatio))),
  );
  const litThreshold = sorted[idx] ?? Infinity;
  const maxScore = sorted[sorted.length - 1] ?? 1;
  const span = Math.max(0.001, maxScore - litThreshold);

  for (const cell of cells) {
    const { wx, wy, ww, wh, score, toneIdx } = cell;
    if (score < litThreshold) continue;
    const u = Math.min(1, Math.max(0, (score - litThreshold) / span));
    const amber = NIGHT_AMBER_GLASS[toneIdx];
    const litT = isSubtle
      ? 0.04 + u * 0.02
      : u > 0.6
        ? 0.84 + (u - 0.6) * 0.3
        : u > 0.24
          ? 0.58 + (u - 0.24) * 0.55
          : 0.36 + u * 0.75;

    ctx.fillStyle = mixHex(base, amber, Math.min(0.95, litT));
    ctx.fillRect(wx, wy, ww, wh);
    if (!isSubtle) {
      ctx.fillStyle = mixHex(base, "#ffd79a", Math.min(0.98, litT + 0.12));
      ctx.fillRect(wx, wy, ww, 1);
    }
  }

  return ctx.getImageData(0, 0, size, size);
}

const facadeImageName = (theme: Theme, variant: FacadeVariant, lod: FacadeLod) =>
  `facade-${theme}-${variant}-${lod}`;

const selectedFacadePatternExpr = (
  theme: Theme,
  s: ColorStops | null,
  lod: FacadeLod,
) => {
  if (theme === "dark") return facadeImageName("dark", "selected", lod) as never;
  const stops = s ?? DEFAULT_COLOR_STOPS;
  return [
    "step",
    ["coalesce", ["get", "index"], stops.mid],
    facadeImageName("light", "selected-vlow", lod),
    stops.t1,
    facadeImageName("light", "selected-low", lod),
    stops.t2,
    facadeImageName("light", "selected-mid", lod),
    stops.t3,
    facadeImageName("light", "selected-high", lod),
    stops.t4,
    facadeImageName("light", "selected-vhigh", lod),
  ] as never;
};

// Тот же step, что и у цвета: индексы → ступени для жилых, нейтральный
// контекстный вариант (без окон) — для нежилых.
// fill-extrusion-pattern в MapLibre не поддерживает feature-state, поэтому
// состояние выбора передаём явно через activeSelectedId.
const facadePatternExpr = (
  theme: Theme,
  s: ColorStops | null,
  lod: FacadeLod,
  activeSelectedId: number | null = null,
  dimNonHome = false,
) => {
  const stops = s ?? DEFAULT_COLOR_STOPS;
  const otherVariant: FacadeVariant = dimNonHome ? "other" : "other-overview";
  const stepExpr = [
    "step",
    ["coalesce", ["get", "index"], stops.mid],
    facadeImageName(theme, "vlow", lod),
    stops.t1,
    facadeImageName(theme, "low", lod),
    stops.t2,
    facadeImageName(theme, "mid", lod),
    stops.t3,
    facadeImageName(theme, "high", lod),
    stops.t4,
    facadeImageName(theme, "vhigh", lod),
  ];
  const homeOrOtherExpr = [
    "case",
    isNonHomeBuildingExpr(),
    facadeImageName(theme, otherVariant, lod),
    stepExpr,
  ];
  if (activeSelectedId === null) {
    return homeOrOtherExpr as never;
  }
  return [
    "case",
    ["==", ["coalesce", ["to-number", ["get", "id"]], ["id"]], activeSelectedId],
    selectedFacadePatternExpr(theme, s, lod),
    homeOrOtherExpr,
  ] as never;
};

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
      "source-layer": "landuse",
      filter: BM_LAYER("landuse"),
      paint: { "fill-color": p.landUse, "fill-opacity": 0.75 },
    },
    {
      id: "bm-green",
      type: "fill",
      source: "basemap",
      "source-layer": "green",
      filter: BM_LAYER("green"),
      paint: {
        "fill-color": ["match", ["get", "class"], "wood", p.wood, p.park],
        "fill-opacity": 0.9,
      },
    },
    {
      // Деревья — декоративные точки в park/wood (оформление, не факт).
      // В плоскости карты (pitch-alignment/scale: map) и три ступени размера по зуму:
      // z < 14.6 / 14.6–16.2 / >= 16.2 — кроны уходят в перспективу и не режутся краем.
      id: "bm-trees",
      type: "circle",
      source: "trees",
      paint: {
        "circle-pitch-alignment": "map",
        "circle-pitch-scale": "map",
        "circle-color": ["match", ["get", "tone"], 1, p.treeDark, p.tree] as never,
        "circle-radius": [
          "step",
          ["zoom"],
          ["*", 2.6, ["get", "size"]],
          14.6,
          ["*", 4.4, ["get", "size"]],
          16.2,
          ["*", 7.2, ["get", "size"]],
        ] as never,
        "circle-stroke-color": p.treeDark,
        "circle-stroke-width": ["step", ["zoom"], 0.5, 14.6, 0.85, 16.2, 1.2] as never,
        "circle-opacity": 0.95,
        "circle-blur": 0.14,
      },
    },
    {
      id: "bm-water",
      type: "fill",
      source: "basemap",
      "source-layer": "water",
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
      "source-layer": "river",
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
      "source-layer": "waterway",
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
      "source-layer": "road",
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
      "source-layer": "road",
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
      "source-layer": "road",
      minzoom: 10,
      filter: BM_ROAD("major"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadMajorCasing,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 2.8, 14, 5.5, 16, 9.5, 18, 16],
      },
    },
    {
      id: "bm-road-major",
      type: "line",
      source: "basemap",
      "source-layer": "road",
      minzoom: 10,
      filter: BM_ROAD("major"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadMajor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.6, 14, 3.4, 16, 6.2, 18, 11],
      },
    },
    {
      // Осевая разметка на магистралях: белый пунктир вдоль готовой
      // геометрии дороги, не отдельные объекты.
      id: "bm-road-marking",
      type: "line",
      source: "basemap",
      "source-layer": "road",
      minzoom: 13,
      filter: BM_ROAD("major"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": p.roadMark,
        "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 16, 1.2, 18, 1.8],
        "line-opacity": 0.7,
        "line-dasharray": [2, 4],
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
    "bm-trees": {
      "circle-color": ["match", ["get", "tone"], 1, p.treeDark, p.tree],
      "circle-stroke-color": p.treeDark,
    },
    "bm-water": { "fill-color": p.water, "fill-outline-color": p.waterLine },
    "bm-river": { "line-color": p.water },
    "bm-waterway": { "line-color": p.waterLine },
    "bm-road-minor-casing": { "line-color": p.roadCasing },
    "bm-road-minor": { "line-color": p.roadMinor },
    "bm-road-casing": { "line-color": p.roadMajorCasing },
    "bm-road-major": { "line-color": p.roadMajor },
    "bm-road-marking": { "line-color": p.roadMark },
    "glow-outer": { "circle-color": p.selected },
    "glow-inner": { "circle-color": p.selected },
    "buildings-shadow-1": { "fill-color": p.shadow },
    "buildings-shadow-2": { "fill-color": p.shadow },
    "buildings-shadow-3": { "fill-color": p.shadow },
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
  // Шаг B1: разбивка индекса теперь лежит рядом с маршрутами, а не в тайлах.
  breakdown?: BuildingBreakdown;
};
type FlatRoutes = Record<string, { minutes: number; coordinates: Position[] }>;
const ROUTES_URL = (districtId: number, shard: number) =>
  `data/routes/${districtId}/${shard}.json.gz`;
const ISOCHRONE_URL = (districtId: number, shard: number) =>
  `data/isochrones/${districtId}/${shard}.json.gz`;
// Изохроны 5/10/15 минут — шард выбранного района.
type IsoGeometry = { type: "Polygon" | "MultiPolygon"; coordinates: unknown };
type IsochroneData = Record<string, IsoGeometry>;
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

function ringAreaHectares(ring: Position[]): number {
  if (ring.length < 3) return 0;
  const latRad = (55.756 * Math.PI) / 180;
  const lonM = Math.cos(latRad) * 111320;
  const latM = 111132;
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    sum += (ring[j][0] - ring[i][0]) * lonM * ((ring[j][1] + ring[i][1]) * 0.5 * latM);
  }
  return Math.abs(sum) / 10000;
}

// Декоративные деревья в park и wood: детерминированные точки внутри полигонов
// (seeded PRNG от стабильного идентификатора полигона, 60–80 точек на гектар),
// чтобы кроны лежали в плоскости карты и уходили в перспективу без среза по краю.
// Это оформление, не факт из данных: в карточку и индекс не попадает.
function buildTreePoints(
  features: GFeature<BasemapProps>[],
): GCollection<{ size: number; tone: number }> {
  const points: GFeature<{ size: number; tone: number }>[] = [];
  for (let fi = 0; fi < features.length; fi++) {
    const f = features[fi];
    if (f.properties.layer !== "green") continue;
    const cls = f.properties.class;
    if (cls !== "park" && cls !== "wood") continue;
    let polys: Position[][][] = [];
    if (f.geometry.type === "Polygon") polys = [f.geometry.coordinates];
    else if (f.geometry.type === "MultiPolygon") polys = f.geometry.coordinates;
    else continue;
    for (let pi = 0; pi < polys.length; pi++) {
      const rings = polys[pi];
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const [x, y] of rings[0]) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }
      const areaHa = ringAreaHectares(rings[0]);
      if (areaHa < 0.04) continue;
      // Seed от id/координат полигона — детерминированный и независимый от порядка обхода.
      const rawId = (f as { id?: number | string }).id;
      const numId = typeof rawId === "number" ? rawId : Number(rawId) || 0;
      let seed =
        ((Math.round(minX * 1e5) * 73856093) ^
          (Math.round(minY * 1e5) * 19349663) ^
          ((numId || fi + 1) * 83492791) ^
          (pi * 2654435761)) >>>
        0;
      const rand = () => {
        seed = (seed * 1664525 + 1013904223) >>> 0;
        return seed / 4294967296;
      };
      // 60–80 точек на гектар (в wood чуть плотнее, чем в park).
      const densityPerHa = cls === "wood" ? 78 : 70;
      const want = Math.min(340, Math.max(5, Math.round(areaHa * densityPerHa)));
      let made = 0;
      let tries = 0;
      while (made < want && tries < want * 9) {
        tries++;
        const pt: [number, number] = [
          minX + rand() * (maxX - minX),
          minY + rand() * (maxY - minY),
        ];
        if (!polygonContains(pt, rings)) continue;
        points.push({
          type: "Feature",
          properties: {
            size: 0.78 + rand() * 0.62,
            tone: rand() < (cls === "wood" ? 0.45 : 0.3) ? 1 : 0,
          },
          geometry: { type: "Point", coordinates: pt },
        });
        made++;
        if (points.length >= 7500) return { type: "FeatureCollection", features: points };
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
  is_home?: boolean | number | string;
  breakdown?: BuildingBreakdown | string;
};

function parseBuildingBreakdown(value: unknown): BuildingBreakdown | null {
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      return parsed && typeof parsed === "object"
        ? (parsed as BuildingBreakdown)
        : null;
    } catch {
      return null;
    }
  }
  return value && typeof value === "object"
    ? (value as BuildingBreakdown)
    : null;
}

// Метаданные из компактного индекса и выбранного vector feature.
type BuildingMeta = {
  id: number;
  center: [number, number] | null;
  address: string | null;
  index: number | null;
  year: number | null;
  isHome?: boolean;
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
  const [notice, setNotice] = useState<string | null>(null);
  const [nonHomeNotice, setNonHomeNotice] = useState<{
    point: [number, number];
    status: "idle" | "loading" | "error" | "far";
  } | null>(null);
  const [theme, setTheme] = useState<Theme>("light"); // стабильно для SSR/гидрации
  const [mounted, setMounted] = useState(false);
  const [fatalError, setFatalError] = useState<string | null>(null);
  // Splash: "show" → "fade" (карта готова или 2 с) → "gone".
  const [splash, setSplash] = useState<"show" | "fade" | "gone">("show");
  const deselectRef = useRef<(() => void) | null>(null);
  const selectRef = useRef<((id: number) => void) | null>(null);
  const loadSearchIndexRef = useRef<(() => Promise<void>) | null>(null);
  const findNearestHomeRef = useRef<((point: [number, number]) => Promise<void>) | null>(null);
  const applyVisibilityRef = useRef<((v: Visibility) => void) | null>(null);
  const applyThemeRef = useRef<((t: Theme) => void) | null>(null);
  const selectedRef = useRef<SelectedBuilding | null>(null);
  const visibleRef = useRef<Visibility>(DEFAULT_VISIBILITY);
  // Маршруты выбранного здания (Valhalla) и кэш по id.
  // TS7 (tsgo): глобальный new Map<K,V>() ломается TS7009 — Record (decisions.md).
  // undefined = не запрашивали, null = файла нет (не повторять), иначе маршруты.
  const routesRef = useRef<FlatRoutes | null>(null);
  const routesCacheRef = useRef<Record<number, FlatRoutes | null>>({});
  const routesShardCacheRef = useRef<
    Record<string, Promise<Record<string, BuildingRoutes> | null>>
  >({});
  // breakdown из routes/<id>.json (режим тайлов): undefined = не запрашивали.
  const breakdownCacheRef = useRef<Record<number, BuildingBreakdown | null>>({});
  // Изохрона: минуты выбраны в карточке, геометрия — по файлу дома.
  const [contour, setContour] = useState<number | null>(null);
  const isoShardCacheRef = useRef<
    Record<string, Promise<Record<string, IsochroneData> | null>>
  >({});
  const districtByBuildingRef = useRef<Record<number, number>>({});
  const isoGeomRef = useRef<IsoGeometry | null>(null);
  const setIsoSourceRef = useRef<((geom: IsoGeometry | null) => void) | null>(null);
  const noticeTimerRef = useRef<number | null>(null);
  // Реальная тема для карты доступна сразу (карта — клиент-only).
  const themeRef = useRef<Theme>(initialTheme());

  const updateSelected = (b: SelectedBuilding | null) => {
    selectedRef.current = b;
    setSelected(b);
  };

  const showNotice = (message: string) => {
    setNotice(message);
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => {
      setNotice((current) => (current === message ? null : current));
      noticeTimerRef.current = null;
    }, 4000);
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
    const districtId = districtByBuildingRef.current[id];
    if (districtId === undefined) {
      apply(null);
      return;
    }
    const key = `${districtId}/${id % 16}`;
    const shard =
      isoShardCacheRef.current[key] ??
      (isoShardCacheRef.current[key] = fetchGzipJson<
        Record<string, IsochroneData>
      >(ISOCHRONE_URL(districtId, id % 16)).catch(() => null));
    void shard.then((data) => apply(data?.[String(id)] ?? null));
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

      let poi!: GCollection<PoiProps>;
      let redzones!: GCollection<RedzoneProps>;
      const tileMode = true;
      let districts: DistrictEntry[] = [];
      let buildingMeta: BuildingMeta[] = [];
      let metaById: Record<number, BuildingMeta> = {};
      let allIds: number[] = [];
      let buildingCenters: Record<number, [number, number]> = {};
      const colorStops: ColorStops = DEFAULT_COLOR_STOPS;
      let searchIndexData: CompactSearchIndex | null = null;
      let indexPromise: Promise<CompactSearchIndex> | null = null;

      const ensureSearchIndex = (): Promise<CompactSearchIndex> => {
        if (searchIndexData) return Promise.resolve(searchIndexData);
        if (indexPromise) return indexPromise;
        indexPromise = (async () => {
          const index = await fetchGzipJson<CompactSearchIndex>(INDEX_URL);
          const lengths = [
            index.ids?.length,
            index.lon?.length,
            index.lat?.length,
            index.addr?.length,
          ];
          if (
            index.v !== 1 ||
            lengths.some((length) => !Number.isInteger(length)) ||
            new Set(lengths).size !== 1 ||
            index.ids.length === 0
          ) {
            throw new Error("Компактный индекс пуст или повреждён");
          }

          searchIndexData = index;
          buildingMeta = index.ids.map((id, i) => {
            const meta: BuildingMeta = {
              id,
              center: [index.lon[i], index.lat[i]],
              address: index.addr[i] ?? null,
              index: null,
              year: null,
              isHome: true,
            };
            metaById[id] = meta;
            buildingCenters[id] = meta.center as [number, number];
            return meta;
          });
          allIds = buildingMeta.map((meta) => meta.id);
          if (!cancelled) {
            setSearchItems(
              buildingMeta
                .filter((meta): meta is BuildingMeta & { address: string } => Boolean(meta.address))
                .map((meta) => ({
                  id: meta.id,
                  address: meta.address,
                  index: meta.index,
                  year: meta.year,
                })),
            );
          }
          return index;
        })().catch((error) => {
          indexPromise = null;
          throw error;
        });
        return indexPromise;
      };

      const loadSearchIndex = (): Promise<void> =>
        ensureSearchIndex()
          .then(() => undefined)
          .catch((error) => {
            console.error("Индекс поиска не загрузился:", error);
            showNotice("Не удалось загрузить поиск. Попробуйте ещё раз.");
          });
      loadSearchIndexRef.current = loadSearchIndex;

      try {
        const [buildingsHead, basemapHead] = await Promise.all([
          fetch(BUILDINGS_URL, { method: "HEAD" }),
          fetch(BASEMAP_URL, { method: "HEAD" }),
        ]);
        if (
          !buildingsHead.ok ||
          !basemapHead.ok ||
          buildingsHead.headers.get("content-length") === "0" ||
          basemapHead.headers.get("content-length") === "0"
        ) {
          throw new Error("Городские PMTiles отсутствуют или пусты");
        }

        const manifest = (await fetchJson(MANIFEST_URL)) as DistrictManifest;
        if (manifest.v !== 1 || !Array.isArray(manifest.districts) || !manifest.districts.length) {
          throw new Error("Манифест районов пуст или повреждён");
        }
        districts = manifest.districts;
        const districtLayers = await Promise.all(
          districts.map(async (district) => {
            const [districtPoi, districtRedzones] = await Promise.all([
              fetchJson(`data/districts/${district.id}/poi.geojson`),
              fetchJson(`data/districts/${district.id}/redzones.geojson`),
            ]);
            return {
              poi: districtPoi as GCollection<PoiProps>,
              redzones: districtRedzones as GCollection<RedzoneProps>,
            };
          }),
        );
        poi = {
          type: "FeatureCollection",
          features: districtLayers.flatMap((data) => data.poi.features),
        };
        redzones = {
          type: "FeatureCollection",
          features: districtLayers.flatMap((data) => data.redzones.features),
        };
      } catch (e) {
        console.error("Не удалось загрузить данные карты:", e);
        setFatalError(
          "Не удалось загрузить городские тайлы или данные районов. Проверьте соединение и обновите страницу.",
        );
        return;
      }
      if (cancelled) return;

      // Протокол PMTiles регистрируем один раз за жизнь вкладки.
      if (!pmtilesProtocolReady) {
        maplibregl.addProtocol("pmtiles", new Protocol().tile);
        pmtilesProtocolReady = true;
      }

      const palette = MAP_THEMES[themeRef.current];

      const poiCenters = poi.features
        .map((f) => ({ f, center: geometryCenter(f.geometry) }))
        .filter(
          (x): x is { f: GFeature<PoiProps>; center: [number, number] } =>
            x.center !== null,
      );
      const redzoneFeatures = redzones.features;

      let labelCandidates: LabelCandidate[] = [];

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

      // Векторный источник требует явного слоя tippecanoe; geojson — нет.
      const buildingSourceLayer = tileMode ? { "source-layer": "buildings" } : {};

      try {
        map = new maplibregl.Map({
        container,
        // На 1× мониторах (десктоп) без сглаживания кромки зданий и подписей
        // «рвутся». На телефонах DPR 2–3 уже даёт сглаживание, поэтому там
        // antialias не включаем — экономим батарею и GPU.
        // В MapLibre 6 опция живёт в canvasContextAttributes (top-level
        // antialias удалён), остальные атрибуты контекста берутся по умолчанию.
        canvasContextAttributes: {
          antialias:
            typeof window !== "undefined" && window.devicePixelRatio < 1.5,
        },
        style: {
          version: 8,
          light: MAP_LIGHT[themeRef.current],
          sources: {
            basemap: {
              type: "vector",
              url: `pmtiles://${new URL(BASEMAP_URL, window.location.href).href}`,
            },
            trees: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
            glow: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
              // Отдельная геометрия выбранного дома и тонкого контура его крыши (~1.5 px).
              selectedBuilding: {
                type: "geojson",
                maxzoom: 16,
                data: { type: "FeatureCollection", features: [] },
              },
              selectedRoofEdge: {
                type: "geojson",
                maxzoom: 16,
                data: { type: "FeatureCollection", features: [] },
              },
            buildings: {
              type: "vector",
              // addProtocol ждёт абсолютный URL, страница живёт в подпапке.
              url: `pmtiles://${new URL(BUILDINGS_URL, window.location.href).href}`,
            },
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
              id: "redzones-glow",
              type: "line",
              source: "redzones",
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.redzone,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 9, 17.5, 24],
                "line-blur": ["interpolate", ["linear"], ["zoom"], 14, 7, 17.5, 18],
                "line-opacity": [
                  "interpolate",
                  ["linear"],
                  ["zoom"],
                  14,
                  themeRef.current === "dark" ? 0.26 : 0.22,
                  17.5,
                  themeRef.current === "dark" ? 0.36 : 0.3,
                ],
              },
            },
            {
              id: "redzones-fill",
              type: "fill",
              source: "redzones",
              filter: ["==", ["geometry-type"], "Polygon"],
              paint: {
                "fill-color": palette.redzone,
                "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.1, 17.5, 0.16],
              },
            },
            {
              id: "redzones-road",
              type: "line",
              source: "redzones",
              filter: ["!=", ["get", "kind"], "railway"],
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.redzone,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 1.6, 17.5, 2.8],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.48, 17.5, 0.62],
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
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 1.6, 17.5, 2.8],
                "line-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.48, 17.5, 0.6],
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
              // Мягкое свечение у основания выбранного дома (эллипс в плоскости земли):
              // ночь — тёпло-зелёное, суммарная opacity 0.20–0.25; день — 0.10–0.12.
              id: "glow-outer",
              type: "circle",
              source: "glow",
              paint: {
                "circle-color": palette.selected,
                "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 28, 17, 76],
                "circle-opacity": themeRef.current === "dark" ? 0.13 : 0.06,
                "circle-blur": 1,
                "circle-pitch-alignment": "map",
              },
            },
            {
              id: "glow-inner",
              type: "circle",
              source: "glow",
              paint: {
                "circle-color": palette.selected,
                "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 14, 17, 38],
                "circle-opacity": themeRef.current === "dark" ? 0.1 : 0.05,
                "circle-blur": 0.85,
                "circle-pitch-alignment": "map",
              },
            },
            // Мягкая многослойная тень под домами: 3 плоских fill со сдвигом
            // [3,3]..[6,6], opacity 0.05..0.10 (light), без обводки и только z >= 14.
            ...BUILDING_SHADOW_LAYERS.map((s) => ({
              id: s.id,
              type: "fill" as const,
              source: "buildings",
              minzoom: 14,
              ...buildingSourceLayer,
              paint: {
                "fill-color": palette.shadow,
                "fill-opacity": s.opacity[themeRef.current],
                "fill-translate": s.translate,
                "fill-translate-anchor": "viewport" as const,
                "fill-antialias": false,
              },
            })),
            {
              // Мягкое свечение по footprint у основания выбранного дома.
              id: "outline-halo",
              type: "line",
              source: "buildings",
              ...buildingSourceLayer,
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.halo,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 10, 17, 22],
                "line-blur": ["interpolate", ["linear"], ["zoom"], 14, 7, 17, 15],
                "line-opacity": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  themeRef.current === "dark" ? 0.18 : 0.09,
                  0,
                ],
              },
            },
            {
              // Спокойный тонкий контур у основания выбранного дома (1.6 px, opacity 0.55).
              id: "outline-core",
              type: "line",
              source: "buildings",
              ...buildingSourceLayer,
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.outlineCore,
                "line-width": 1.6,
                "line-opacity": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0.55,
                  0,
                ],
              },
            },
            // V4b6: три концентрических обводки по footprint выбранного дома
            // на земле, под зданиями. Источник selectedBuilding уже содержит
            // полный контур, поэтому свет переезжает вместе с выбором.
            ...GLOW_RINGS.map((ring) => ({
              id: ring.id,
              type: "line" as const,
              source: "selectedBuilding",
              minzoom: FACADE_COARSE_MIN_ZOOM,
              layout: {
                "line-cap": "round" as const,
                "line-join": "round" as const,
              },
              paint: {
                "line-color": GLOW_GROUND_COLOR,
                "line-width": glowZoomScale(ring.width) as never,
                "line-blur": ring.blur,
                "line-opacity": glowRingOpacity(
                  ring.opacity,
                  themeRef.current,
                ) as never,
              },
            })),
            {
              // Яркая «неоновая» кромка по краю footprint — без blur.
              id: GLOW_EDGE_LAYER_ID,
              type: "line",
              source: "selectedBuilding",
              minzoom: FACADE_COARSE_MIN_ZOOM,
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": GLOW_EDGE_COLOR,
                "line-width": ["interpolate", ["linear"], ["zoom"], 15.4, 2, 18, 2.5] as never,
                "line-opacity": themeRef.current === "dark" ? 0.5 : 0.08,
              },
            },
            {
              id: "buildings",
              type: "fill-extrusion",
              source: "buildings",
              ...buildingSourceLayer,
              paint: {
                // fill-extrusion-color поддерживает feature-state,
                // fill-extrusion-opacity — нет, поэтому затемнение через цвет.
                "fill-extrusion-color": buildingColorExpr(
                  palette,
                  colorStops,
                  themeRef.current === "dark" ? palette.selectedBody : null,
                ),
                "fill-extrusion-height": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0,
                  ["coalesce", ["get", "height"], 9],
                ] as never,
                "fill-extrusion-base": 0,
                "fill-extrusion-opacity": 0.95,
                "fill-extrusion-vertical-gradient": true,
              },
            },
            {
              // Выбранный дом: корпус со спокойными окнами в обеих темах.
              id: "buildings-selected-body",
              type: "fill-extrusion",
              source: "selectedBuilding",
              paint: {
                "fill-extrusion-color":
                  themeRef.current === "dark"
                    ? palette.selectedBody
                    : (selectedStepColorsExpr(palette, colorStops, 0) as never),
                "fill-extrusion-base": 0,
                "fill-extrusion-height": ["coalesce", ["get", "height"], 9] as never,
                "fill-extrusion-opacity": 0.95,
                "fill-extrusion-vertical-gradient": true,
              },
            },
            {
              // Крыша обычных домов: тонкая плита поверх фасада (в светлой теме — почти нейтральная светлая).
              id: "buildings-roof",
              type: "fill-extrusion",
              source: "buildings",
              minzoom: 14,
              ...buildingSourceLayer,
              paint: {
                "fill-extrusion-color": buildingRoofColorExpr(palette, colorStops),
                "fill-extrusion-base": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0,
                  ["coalesce", ["get", "height"], 9],
                ] as never,
                "fill-extrusion-height": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0,
                  ["+", ["coalesce", ["get", "height"], 9], 0.5],
                ] as never,
                "fill-extrusion-opacity": 0.95,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            {
              // Тонкий контур крыши выбранного дома (~1.5 px, спокойный зелёный, opacity 0.7):
              // лежит чуть ниже крыши buildings-selected-roof и выступает только по внешнему периметру.
              id: "buildings-selected-roof-edge",
              type: "fill-extrusion",
              source: "selectedRoofEdge",
              paint: {
                "fill-extrusion-color": palette.outlineCore,
                "fill-extrusion-base": ["coalesce", ["get", "height"], 9] as never,
                "fill-extrusion-height": ["+", ["coalesce", ["get", "height"], 9], 0.28] as never,
                "fill-extrusion-opacity": 0.7,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            {
              // Крыша выбранного дома: яркий цвет ступени дома (на 5% светлее), не мятная.
              // Перекрывает внутреннюю часть крыши и любые стыки тайлов.
              id: "buildings-selected-roof",
              type: "fill-extrusion",
              source: "selectedBuilding",
              paint: {
                "fill-extrusion-color": selectedStepColorsExpr(palette, colorStops, 0.05) as never,
                "fill-extrusion-base": ["coalesce", ["get", "height"], 9] as never,
                "fill-extrusion-height": ["+", ["coalesce", ["get", "height"], 9], 0.35] as never,
                "fill-extrusion-opacity": 1,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            {
              // V4b6: подсветка нижнего пояса стен (0.6–0.9 м). Выше — обычный
              // фасад. Только тёмная тема, над зданиями и под маршрутами/зонами.
              id: GLOW_BASE_LAYER_ID,
              type: "fill-extrusion",
              source: "selectedBuilding",
              minzoom: FACADE_COARSE_MIN_ZOOM,
              layout: {
                visibility: themeRef.current === "dark" ? "visible" : "none",
              },
              paint: {
                "fill-extrusion-color": GLOW_GROUND_COLOR,
                "fill-extrusion-base": 0,
                "fill-extrusion-height": 0.9,
                "fill-extrusion-opacity": 0.2,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            {
              id: "routes-glow",
              type: "line",
              source: "routes",
              filter: ["==", ["geometry-type"], "LineString"],
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.route,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 10, 17.5, 24],
                "line-blur": ["interpolate", ["linear"], ["zoom"], 14, 6, 17.5, 15],
                "line-opacity": [
                  "interpolate",
                  ["linear"],
                  ["zoom"],
                  14,
                  themeRef.current === "dark" ? 0.26 : 0.22,
                  17.5,
                  themeRef.current === "dark" ? 0.36 : 0.3,
                ],
              },
            },
            {
              id: "routes-line",
              type: "line",
              source: "routes",
              filter: ["==", ["geometry-type"], "LineString"],
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.route,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 3.2, 17.5, 5.8],
                "line-opacity": 0.88,
              },
            },
            {
              id: "routes-dots",
              type: "line",
              source: "routes",
              filter: ["==", ["geometry-type"], "LineString"],
              layout: { "line-cap": "round", "line-join": "round" },
              paint: {
                "line-color": palette.routeCore,
                "line-width": ["interpolate", ["linear"], ["zoom"], 14, 2, 17.5, 3.6],
                "line-opacity": 0.95,
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
      let basemapRefreshPending = false;
      const refreshBasemapDecorations = () => {
        if (cancelled || !map || basemapRefreshPending) return;
        basemapRefreshPending = true;
        requestAnimationFrame(() => {
          basemapRefreshPending = false;
          if (cancelled || !map) return;
          const seen = new Set<string>();
          const features: GFeature<BasemapProps>[] = [];
          for (const sourceLayer of [
            "landuse",
            "green",
            "water",
            "river",
            "waterway",
            "road",
          ]) {
            for (const feature of map.querySourceFeatures("basemap", { sourceLayer })) {
              const properties = feature.properties as unknown as BasemapProps;
              const geometryKey = JSON.stringify(feature.geometry);
              const key = `${properties.layer}/${properties.class}/${properties.name ?? ""}/${geometryKey}`;
              if (seen.has(key)) continue;
              seen.add(key);
              let stableId = 2166136261;
              for (let i = 0; i < key.length; i++) {
                stableId = Math.imul(stableId ^ key.charCodeAt(i), 16777619);
              }
              features.push({
                type: "Feature",
                geometry: feature.geometry,
                properties,
                ...{ id: feature.id ?? (stableId >>> 0) },
              } as GFeature<BasemapProps>);
            }
          }
          labelCandidates = buildLabelCandidates(features);
          (map.getSource("trees") as maplibregl.GeoJSONSource | null)?.setData(
            buildTreePoints(features) as never,
          );
          rebuildLabels();
        });
      };
      map.on("sourcedata", (event) => {
        if (
          event.sourceId === "basemap" &&
          (event.tile || event.isSourceLoaded)
        ) {
          refreshBasemapDecorations();
        }
      });
      map.on("idle", refreshBasemapDecorations);
      map.on("moveend", refreshBasemapDecorations);

      // Источник изохроны: максимум одна фича (полигон выбранного времени).
      setIsoSourceRef.current = (geom) => {
        const src = map?.getSource("isochrone") as maplibregl.GeoJSONSource | null;
        if (!src) return;
        src.setData({
          type: "FeatureCollection",
          features: geom ? [{ type: "Feature", properties: {}, geometry: geom }] : [],
        } as never);
      };
      map.on("error", (e) => {
        console.error("map error:", e.error ?? e);
        const sourceId = (e as unknown as { sourceId?: string }).sourceId;
        if (sourceId === "buildings" || sourceId === "basemap") {
          setFatalError(
            "Не удалось прочитать городские PMTiles. Обновите страницу или сообщите о проблеме.",
          );
        }
      });
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
            const d = Number.isFinite(entry.distance_m) ? Math.round(entry.distance_m) : null;
            const text = `${PENALTY_LABELS[key] ?? key} · ${d !== null && d >= 0 ? `${d} м` : "рядом"}`;
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
            const endPt = coordinates[coordinates.length - 1] ?? target.center;
            routeFeatures.push({
              type: "Feature",
              properties: { category: cat, kind: "endpoint" },
              geometry: { type: "Point", coordinates: endPt },
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
          map.setLayoutProperty("redzones-glow", "visibility", rz);
          map.setLayoutProperty("redzones-road", "visibility", rz);
          map.setLayoutProperty("redzones-rail", "visibility", rz);
          map.setLayoutProperty("redzones-fill", "visibility", rz);
        }
        if (map.getLayer("routes-line")) {
          const rt = v.routes ? "visible" : "none";
          map.setLayoutProperty("routes-glow", "visibility", rt);
          map.setLayoutProperty("routes-line", "visibility", rt);
          map.setLayoutProperty("routes-dots", "visibility", rt);
        }
        rebuildOverlays(selectedRef.current, v);
      };

      const addFacadeImages = (t: Theme) => {
        if (!map) return;
        const p = MAP_THEMES[t];
        const selectedStepColor: Record<FacadeStep, string> = {
          vlow: p.selectedVlow,
          low: p.selectedLow,
          mid: p.selectedMid,
          high: p.selectedHigh,
          vhigh: p.selectedVhigh,
        };
        const lods: FacadeLod[] = ["flat", "subtle", "coarse", "fine"];
        const add = (variant: FacadeVariant, baseColor: string, lod: FacadeLod) => {
          const name = facadeImageName(t, variant, lod);
          if (map?.getImage(name)) return;
          map?.addImage(name, makeFacadeImage(baseColor, t, variant, lod), {
            pixelRatio: facadePixelRatio(lod),
          });
        };
        for (const lod of lods) {
          for (const step of FACADE_STEPS) {
            add(step, p[step], lod);
            if (t === "light") {
              add(`selected-${step}` as FacadeSelectedStep, selectedStepColor[step], lod);
            }
          }
          add("dim", p.dim, lod);
          add("other", p.other, lod);
          add("other-overview", p.otherOverview, lod);
          add("selected", p.selectedBody, lod);
        }
      };

      let currentSelectedFeatures: maplibregl.GeoJSONFeature[] = [];
      let currentSelectedId: number | null = null;
      let currentSelectedSignature: string | null = null;
      let currentSelectedArea = 0;
      let pendingFacadeBearingId: number | null = null;
      let currentRoofEdgeZoom: number | null = null;
      const selectedWindowCache = new globalThis.Map<
        number,
        { signature: string; includeHalo: boolean; data: FeatureCollection }
      >();
      const cacheSelectedWindows = (
        id: number,
        entry: { signature: string; includeHalo: boolean; data: FeatureCollection },
      ) => {
        selectedWindowCache.delete(id);
        selectedWindowCache.set(id, entry);
        if (selectedWindowCache.size > 8) {
          const oldest = selectedWindowCache.keys().next().value;
          if (oldest !== undefined) selectedWindowCache.delete(oldest);
        }
      };
      let captureToken = 0;
      let captureRequested = false;
      let captureScheduled = false;
      let selectionMovePending = false;
      let captureFrame: number | null = null;
      let pendingCaptureMoveEnd: (() => void) | null = null;
      let pendingCaptureIdle: (() => void) | null = null;

      const cancelSelectedCapture = () => {
        captureToken++;
        captureRequested = false;
        captureScheduled = false;
        selectionMovePending = false;
        if (captureFrame !== null) {
          window.cancelAnimationFrame(captureFrame);
          captureFrame = null;
        }
        if (pendingCaptureMoveEnd) {
          map?.off("moveend", pendingCaptureMoveEnd);
          pendingCaptureMoveEnd = null;
        }
        if (pendingCaptureIdle) {
          map?.off("idle", pendingCaptureIdle);
          pendingCaptureIdle = null;
        }
        return captureToken;
      };

      const refreshSelectedRoofEdge = () => {
        if (!map) return;
        const edgeSrc = map.getSource("selectedRoofEdge") as maplibregl.GeoJSONSource | null;
        if (!edgeSrc) return;
        const zoom = map.getZoom();
        if (currentRoofEdgeZoom === zoom) return;
        currentRoofEdgeZoom = zoom;
        edgeSrc.setData({
          type: "FeatureCollection",
          features: currentSelectedFeatures.length
            ? buildRoofEdgeFeatures(currentSelectedFeatures, map.getZoom())
            : [],
        } as never);
      };

      const clearSelectedWindows = () => {
        if (!map) return;
        const src = map.getSource(HERO_WINDOWS_SOURCE_ID) as maplibregl.GeoJSONSource | null;
        src?.setData({ type: "FeatureCollection", features: [] } as never);
        (
          window as unknown as { __selectedWindowsData?: FeatureCollection | null }
        ).__selectedWindowsData = null;
      };

      let currentSelectedWindowTheme: Theme | null = null;
      let currentSelectedWindowVisibility: boolean | null = null;
      let currentSelectedHaloVisibility: boolean | null = null;
      const syncSelectedWindowTheme = (t: Theme) => {
        if (!map) return;
        const colorProperty = t === "dark" ? "colorDark" : "colorLight";
        const lodVisible = facadeLodForZoom(map.getZoom()) !== "flat";
        const haloVisible =
          t === "dark" && window.matchMedia("(min-width: 768px)").matches && lodVisible;
        if (
          currentSelectedWindowTheme !== t &&
          map.getLayer(HERO_HALO_LAYER_ID)
        ) {
          map.setPaintProperty(
            HERO_HALO_LAYER_ID,
            "fill-extrusion-color",
            ["get", colorProperty] as never,
          );
        }
        if (
          currentSelectedHaloVisibility !== haloVisible &&
          map.getLayer(HERO_HALO_LAYER_ID)
        ) {
          map.setLayoutProperty(
            HERO_HALO_LAYER_ID,
            "visibility",
            haloVisible ? "visible" : "none",
          );
        }
        if (
          currentSelectedWindowTheme !== t &&
          map.getLayer(HERO_WINDOW_LAYER_ID)
        ) {
          map.setPaintProperty(
            HERO_WINDOW_LAYER_ID,
            "fill-extrusion-color",
            ["get", colorProperty] as never,
          );
        }
        if (
          currentSelectedWindowVisibility !== lodVisible &&
          map.getLayer(HERO_WINDOW_LAYER_ID)
        ) {
          map.setLayoutProperty(
            HERO_WINDOW_LAYER_ID,
            "visibility",
            lodVisible ? "visible" : "none",
          );
        }
        currentSelectedWindowTheme = t;
        currentSelectedWindowVisibility = lodVisible;
        currentSelectedHaloVisibility = haloVisible;
      };

      const rebuildSelectedWindows = (id: number, signature: string) => {
        if (!map) return;
        if (!currentSelectedFeatures.length) {
          clearSelectedWindows();
          return;
        }
        const includeHalo = window.matchMedia("(min-width: 768px)").matches;
        const cached = selectedWindowCache.get(id);
        let data =
          cached?.signature === signature && cached.includeHalo === includeHalo
            ? cached.data
            : null;
        if (data && cached) {
          selectedWindowCache.delete(id);
          selectedWindowCache.set(id, cached);
        } else {
          data = buildSelectedWindowFeatures(
            currentSelectedFeatures,
            colorStops,
            includeHalo,
          );
          cacheSelectedWindows(id, { signature, includeHalo, data });
        }
        (
          window as unknown as { __selectedWindowsData?: FeatureCollection | null }
        ).__selectedWindowsData = data.features.length ? data : null;

        const existingSource = map.getSource(
          HERO_WINDOWS_SOURCE_ID,
        ) as maplibregl.GeoJSONSource | null;
        if (existingSource) {
          existingSource.setData(data as never);
        } else {
          map.addSource(HERO_WINDOWS_SOURCE_ID, {
            type: "geojson",
            maxzoom: 22,
            tolerance: 0,
            data,
          });
        }
        if (!data.features.length) return;

        const beforeId = map.getLayer("buildings-selected-roof-edge")
          ? "buildings-selected-roof-edge"
          : map.getLayer("routes-glow")
            ? "routes-glow"
            : undefined;
        if (!map.getLayer(HERO_HALO_LAYER_ID)) {
          map.addLayer(
            {
              id: HERO_HALO_LAYER_ID,
              type: "fill-extrusion",
              source: HERO_WINDOWS_SOURCE_ID,
              minzoom: FACADE_COARSE_MIN_ZOOM,
              filter: ["==", ["get", "kind"], "halo"],
              layout: { visibility: "none" },
              paint: {
                "fill-extrusion-color": ["get", "colorDark"] as never,
                "fill-extrusion-base": ["get", "base"] as never,
                "fill-extrusion-height": ["get", "height"] as never,
                "fill-extrusion-opacity": 0.14,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            beforeId,
          );
        }
        if (!map.getLayer(HERO_WINDOW_LAYER_ID)) {
          map.addLayer(
            {
              id: HERO_WINDOW_LAYER_ID,
              type: "fill-extrusion",
              source: HERO_WINDOWS_SOURCE_ID,
              minzoom: FACADE_COARSE_MIN_ZOOM,
              filter: ["!=", ["get", "kind"], "halo"],
              layout: { visibility: "none" },
              paint: {
                "fill-extrusion-color": ["get", "colorDark"] as never,
                "fill-extrusion-base": ["get", "base"] as never,
                "fill-extrusion-height": ["get", "height"] as never,
                "fill-extrusion-opacity": 0.96,
                "fill-extrusion-vertical-gradient": false,
              },
            },
            beforeId,
          );
        }
        currentSelectedWindowTheme = null;
        currentSelectedWindowVisibility = null;
        currentSelectedHaloVisibility = null;
        syncSelectedWindowTheme(themeRef.current);
      };

      // Четыре уровня текстуры фасадов по зуму:
      // z < 15.4 ("flat") — чистый цвет без окон и без крыши-плиты обычных домов;
      // z 15.4–16.0 ("subtle") — единичные огни в ночи (≤ 6% контраста);
      // z 16.0–17.0 ("coarse") — редкие огни в ночи и окна выбранного дома;
      // z >= 17.0 ("fine") — крупный план.
      // Pattern не снимаем через null (в MapLibre 6 это ломало рендер слоя),
      // а подменяем набор картинок.
      let selectedId: number | null = null;
      let currentFacadeLod: FacadeLod | null = null;
      let currentNonHomeDim: boolean | null = null;
      let selectedBodyRevealPending = false;
      const syncFacades = (t: Theme) => {
        if (!map || !map.getLayer("buildings")) return;
        refreshSelectedRoofEdge();
        const zoom = map.getZoom();
        const lod = facadeLodForZoom(zoom);
        const dimNonHome = zoom >= NON_HOME_DIM_MIN_ZOOM;
        syncSelectedWindowTheme(t);
        if (lod === currentFacadeLod && dimNonHome === currentNonHomeDim) return;
        currentFacadeLod = lod;
        currentNonHomeDim = dimNonHome;
        addFacadeImages(t);
        map.setPaintProperty(
          "buildings",
          "fill-extrusion-pattern",
          facadePatternExpr(t, colorStops, lod, selectedId, dimNonHome),
        );
        map.setLayoutProperty("buildings-roof", "visibility", "visible");
        if (
          map.getLayer("buildings-selected-body") &&
          !selectedBodyRevealPending
        ) {
          map.setLayoutProperty(
            "buildings-selected-body",
            "visibility",
            "visible",
          );
        }
        // Шейдер pattern-варианта компилируется не в том же кадре, а карта
        // уже остановилась на zoomend. Без повторного кадра слой может
        // остаться невидимым до следующего движения — пинаем ещё раз.
        const kick = () => map?.triggerRepaint();
        requestAnimationFrame(kick);
        window.setTimeout(kick, 250);
      };

      applyThemeRef.current = (t: Theme) => {
        if (!map || !map.getLayer("buildings")) return;
        const p = MAP_THEMES[t];
        map.setLight(MAP_LIGHT[t]);
        map.setPaintProperty(
          "buildings",
          "fill-extrusion-color",
          buildingColorExpr(p, colorStops, t === "dark" ? p.selectedBody : null),
        );
        map.setPaintProperty(
          "buildings",
          "fill-extrusion-height",
          [
            "case",
            ["to-boolean", ["feature-state", "selected"]],
            0,
            ["coalesce", ["get", "height"], 9],
          ] as never,
        );
        map.setPaintProperty(
          "buildings-selected-body",
          "fill-extrusion-color",
          t === "dark" ? p.selectedBody : (selectedStepColorsExpr(p, colorStops, 0) as never),
        );
        map.setPaintProperty(
          "buildings-selected-body",
          "fill-extrusion-base",
          0,
        );
        map.setPaintProperty(
          "buildings-selected-body",
          "fill-extrusion-height",
          ["coalesce", ["get", "height"], 9] as never,
        );
        map.setPaintProperty(
          "buildings-selected-body",
          "fill-extrusion-vertical-gradient",
          true,
        );
        map.setPaintProperty(
          "buildings-roof",
          "fill-extrusion-color",
          buildingRoofColorExpr(p, colorStops),
        );
        if (map.getLayer("buildings-selected-roof")) {
          map.setPaintProperty(
            "buildings-selected-roof",
            "fill-extrusion-color",
            selectedStepColorsExpr(p, colorStops, 0.05) as never,
          );
        }
        if (map.getLayer("buildings-selected-roof-edge")) {
          map.setPaintProperty(
            "buildings-selected-roof-edge",
            "fill-extrusion-color",
            p.outlineCore,
          );
        }
        // У темы свой набор картинок — пере-применяем pattern принудительно.
        // Активную тему готовим заранее: pattern включается на zoomend уже в
        // готовом атласе, иначе первый кадр может пропустить слой.
        addFacadeImages(t);
        syncSelectedWindowTheme(t);
        currentFacadeLod = null;
        currentNonHomeDim = null;
        syncFacades(t);
        for (const s of BUILDING_SHADOW_LAYERS) {
          if (map.getLayer(s.id)) {
            map.setPaintProperty(s.id, "fill-opacity", s.opacity[t]);
          }
        }
        map.setPaintProperty("glow-outer", "circle-opacity", t === "dark" ? 0.13 : 0.06);
        map.setPaintProperty("glow-inner", "circle-opacity", t === "dark" ? 0.1 : 0.05);
        map.setPaintProperty("outline-halo", "line-color", p.halo);
        map.setPaintProperty("outline-halo", "line-opacity", [
          "case",
          ["to-boolean", ["feature-state", "selected"]],
          t === "dark" ? 0.18 : 0.09,
          0,
        ] as never);
        map.setPaintProperty("outline-core", "line-color", p.outlineCore);
        // V4b6: земляное свечение и подсветка низа стен — только тёмная тема.
        for (const ring of GLOW_RINGS) {
          if (!map.getLayer(ring.id)) continue;
          map.setPaintProperty(
            ring.id,
            "line-opacity",
            glowRingOpacity(ring.opacity, t) as never,
          );
        }
        if (map.getLayer(GLOW_EDGE_LAYER_ID)) {
          map.setPaintProperty(
            GLOW_EDGE_LAYER_ID,
            "line-opacity",
            t === "dark" ? 0.5 : 0.08,
          );
        }
        if (map.getLayer(GLOW_BASE_LAYER_ID)) {
          map.setLayoutProperty(
            GLOW_BASE_LAYER_ID,
            "visibility",
            t === "dark" ? "visible" : "none",
          );
        }
        map.setPaintProperty("redzones-fill", "fill-color", p.redzone);
        for (const [id, paint] of Object.entries(basemapPaint(p))) {
          if (!map.getLayer(id)) continue;
          for (const [prop, value] of Object.entries(paint)) {
            map.setPaintProperty(id, prop as never, value as never);
          }
        }
        map.setPaintProperty("redzones-glow", "line-color", p.redzone);
        map.setPaintProperty("redzones-glow", "line-opacity", [
          "interpolate",
          ["linear"],
          ["zoom"],
          14,
          t === "dark" ? 0.26 : 0.22,
          17.5,
          t === "dark" ? 0.36 : 0.3,
        ] as never);
        map.setPaintProperty("redzones-road", "line-color", p.redzone);
        map.setPaintProperty("redzones-rail", "line-color", p.redzone);
        map.setPaintProperty("routes-glow", "line-color", p.route);
        map.setPaintProperty("routes-glow", "line-opacity", [
          "interpolate",
          ["linear"],
          ["zoom"],
          14,
          t === "dark" ? 0.26 : 0.22,
          17.5,
          t === "dark" ? 0.36 : 0.3,
        ] as never);
        map.setPaintProperty("routes-line", "line-color", p.route);
        map.setPaintProperty("routes-dots", "line-color", p.routeCore);
        map.setPaintProperty("isochrone-fill", "fill-color", p.iso);
        map.setPaintProperty("isochrone-line", "line-color", p.iso);
        rebuildOverlays(selectedRef.current, visibleRef.current);
        rebuildLabels();
      };

      // Порог окон: переключаем ровно по окончании зума/движения (и на load через
      // applyThemeRef). Во время инерции карта не дёргается.
      map.on("zoomend", () => syncFacades(themeRef.current));
      map.on("moveend", () => syncFacades(themeRef.current));
      let selectedWindowDesktop = window.matchMedia("(min-width: 768px)").matches;
      map.on("resize", () => {
        const nextDesktop = window.matchMedia("(min-width: 768px)").matches;
        if (nextDesktop === selectedWindowDesktop) return;
        selectedWindowDesktop = nextDesktop;
        const id = currentSelectedId;
        const signature = currentSelectedSignature;
        if (id === null || signature === null) return;
        const rebuildForViewport = () => {
          if (
            currentSelectedId !== id ||
            currentSelectedSignature !== signature
          ) {
            return;
          }
          selectedWindowCache.delete(id);
          rebuildSelectedWindows(id, signature);
        };
        if (map?.isMoving()) map.once("moveend", rebuildForViewport);
        else rebuildForViewport();
      });

      let featureStateInitialized = false;
      let appliedSelectedId: number | null = null;
      const applySelection = (activateSelected = true) => {
        if (!map || !map.getSource("buildings")) return;
        currentFacadeLod = null;
        currentNonHomeDim = null;
        syncFacades(themeRef.current);
        // Векторному источнику нужен sourceLayer и для feature-state.
        const stateSource = tileMode
          ? { source: "buildings", sourceLayer: "buildings" }
          : { source: "buildings" };
        if (featureStateInitialized && selectedId !== null) {
          if (appliedSelectedId !== null && appliedSelectedId !== selectedId) {
            map.setFeatureState(
              { ...stateSource, id: appliedSelectedId },
              { selected: false, inactive: true },
            );
          }
          if (activateSelected && appliedSelectedId !== selectedId) {
            map.setFeatureState(
              { ...stateSource, id: selectedId },
              { selected: true, inactive: false },
            );
          } else if (!activateSelected) {
            map.setFeatureState(
              { ...stateSource, id: selectedId },
              { selected: false, inactive: false },
            );
          }
          appliedSelectedId = activateSelected ? selectedId : null;
          return;
        }
        const idsToStyle =
          allIds.length > 0
            ? allIds
            : map
                .querySourceFeatures("buildings", { sourceLayer: "buildings" })
                .map((feature) => Number(feature.id ?? feature.properties?.id))
                .filter((id) => Number.isFinite(id));
        for (const id of idsToStyle) {
          map.setFeatureState(
            { ...stateSource, id },
            {
              selected: activateSelected && id === selectedId,
              inactive: selectedId !== null && id !== selectedId,
            },
          );
        }
        featureStateInitialized = true;
        appliedSelectedId = activateSelected ? selectedId : null;
      };

      const setSelectedBodyLayerVisible = (visible: boolean) => {
        if (!map?.getLayer("buildings-selected-body")) return;
        map.setLayoutProperty(
          "buildings-selected-body",
          "visibility",
          visible ? "visible" : "none",
        );
      };

      const scheduleSelectedBodyReveal = (id: number, token: number) => {
        const activeMap = map;
        if (
          !activeMap ||
          id !== selectedId ||
          token !== captureToken ||
          currentSelectedId !== id ||
          !currentSelectedFeatures.length
        ) {
          return;
        }
        const reveal = () => {
          pendingCaptureIdle = null;
          if (
            token !== captureToken ||
            id !== selectedId ||
            currentSelectedId !== id ||
            !currentSelectedFeatures.length
          ) {
            return;
          }
          applySelection();
          selectedBodyRevealPending = false;
          setSelectedBodyLayerVisible(true);
        };
        if (pendingCaptureIdle) activeMap.off("idle", pendingCaptureIdle);
        pendingCaptureIdle = reveal;
        activeMap.once("idle", reveal);
      };

      const scheduleSelectedCapture = (
        id: number | null = selectedId,
        token = captureToken,
      ) => {
        const activeMap = map;
        if (
          !activeMap ||
          id === null ||
          id !== selectedId ||
          token !== captureToken
        ) {
          return;
        }
        captureRequested = true;
        if (selectionMovePending) return;
        if (captureScheduled) return;
        if (activeMap.isMoving()) {
          if (!pendingCaptureMoveEnd) {
            const onMoveEnd = () => {
              pendingCaptureMoveEnd = null;
              selectionMovePending = false;
              window.requestAnimationFrame(() => scheduleSelectedCapture(id, token));
            };
            pendingCaptureMoveEnd = onMoveEnd;
            activeMap.once("moveend", onMoveEnd);
          }
          return;
        }

        captureScheduled = true;
        const runCapture = () => {
          if (!captureScheduled) return;
          captureScheduled = false;
          if (pendingCaptureIdle) {
            activeMap.off("idle", pendingCaptureIdle);
            pendingCaptureIdle = null;
          }
          if (
            token !== captureToken ||
            id !== selectedId ||
            !captureRequested
          ) {
            return;
          }
          if (activeMap.isMoving()) {
            scheduleSelectedCapture(id, token);
            return;
          }
          captureRequested = false;
          captureSelectedBody(id, token);
        };
        pendingCaptureIdle = runCapture;
        activeMap.once("idle", runCapture);
        captureFrame = window.requestAnimationFrame(() => {
          captureFrame = null;
          if (
            token === captureToken &&
            id === selectedId &&
            !activeMap.isMoving() &&
            activeMap.loaded()
          ) {
            runCapture();
          }
        });
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

      const setSelectedBody = (features: maplibregl.GeoJSONFeature[]) => {
        const normalized = features.map((feature) => {
          const rawId = feature.id ?? feature.properties?.id;
          const numId = typeof rawId === "number" ? rawId : Number(rawId);
          const meta = Number.isFinite(numId) ? metaById[numId] : undefined;
          return {
            type: "Feature" as const,
            id: rawId,
            geometry: feature.geometry,
            properties: {
              id: rawId,
              height: feature.properties?.height ?? 9,
              index: feature.properties?.index ?? meta?.index ?? 5,
            },
          };
        });
        const nextSignature = normalized.length
          ? normalized.map((f) => geometrySignature(f.geometry)).join("&")
          : null;
        const rawId = normalized[0]?.properties.id;
        const nextId = rawId == null ? null : Number(rawId);
        if (
          nextSignature !== null &&
          nextId === currentSelectedId &&
          nextSignature === currentSelectedSignature
        ) {
          return;
        }
        if (nextSignature === null && currentSelectedSignature === null) return;
        currentSelectedId = nextId;
        currentSelectedSignature = nextSignature;
        currentSelectedArea = normalized.reduce(
          (acc, f) => acc + footprintAreaM2(f.geometry),
          0,
        );
        currentSelectedFeatures = normalized as unknown as maplibregl.GeoJSONFeature[];
        const src = map?.getSource("selectedBuilding") as maplibregl.GeoJSONSource | null;
        src?.setData({
          type: "FeatureCollection",
          features: normalized,
        } as never);
        currentRoofEdgeZoom = null;
        refreshSelectedRoofEdge();
        if (nextId !== null && nextSignature !== null) {
          rebuildSelectedWindows(nextId, nextSignature);
        } else {
          clearSelectedWindows();
        }
      };

      const rotateToFacadeIfSafe = (id: number, geometry: Geometry) => {
        if (!map || pendingFacadeBearingId !== id || selectedId !== id) return;
        pendingFacadeBearingId = null;
        const target = facadeCameraBearing(geometry);
        if (target === null) return;
        const current = map.getBearing();
        const delta = ((((target - current + 180) % 360) + 360) % 360) - 180;
        // Не доворачиваемся, если фасад уже далеко от направления луча;
        // угол — всегда кратчайший, дополнительный предел защищает от рывка.
        if (Math.abs(delta) >= 20) return;
        const adjustment = Math.max(-35, Math.min(35, delta));
        if (Math.abs(adjustment) > 1) {
          map.easeTo({ bearing: current + adjustment, duration: 700 });
        }
      };

      // Геометрия выбранного дома: берём все части с одним id из всех
      // загруженных тайлов (дом режется границами PMTiles) и объединяем их
      // в один footprint, чтобы нормали окон считались по целому контуру.
      const selectedBodyCache = new globalThis.Map<
        number,
        {
          partsSignature: string;
          feature: maplibregl.GeoJSONFeature;
          area: number;
        }
      >();
      const rememberSelectedBody = (
        id: number,
        entry: {
          partsSignature: string;
          feature: maplibregl.GeoJSONFeature;
          area: number;
        },
      ) => {
        selectedBodyCache.delete(id);
        selectedBodyCache.set(id, entry);
        if (selectedBodyCache.size > 8) {
          const oldest = selectedBodyCache.keys().next().value;
          if (oldest !== undefined) selectedBodyCache.delete(oldest);
        }
      };
      let lastCaptureAttemptId: number | null = null;
      let lastCaptureAttemptSignature: string | null = null;
      const captureSelectedBody = (id: number, token: number) => {
        if (!map || map.isMoving() || token !== captureToken || selectedId !== id) return;
        const opts = tileMode ? { sourceLayer: "buildings" } : undefined;
        const matches = (f: maplibregl.GeoJSONFeature) =>
          Number(f.id) === id || Number(f.properties?.id) === id;

        const found: maplibregl.GeoJSONFeature[] = [];
        const seen = new Set<string>();
        const push = (f: maplibregl.GeoJSONFeature) => {
          const key = geometrySignature(f.geometry);
          if (!key || seen.has(key)) return;
          seen.add(key);
          found.push(f);
        };

        for (const f of map.querySourceFeatures("buildings", opts as never)) {
          if (matches(f)) push(f);
        }
        // Запасной путь, если источник ещё не отдал фичи после flyTo.
        for (const f of map.queryRenderedFeatures({ layers: ["buildings"] })) {
          if (matches(f)) push(f);
        }
        if (!found.length) return;

        const partsSignature = [...seen].sort().join("&");
        const cached = selectedBodyCache.get(id);
        if (cached?.partsSignature === partsSignature) {
          selectedBodyCache.delete(id);
          selectedBodyCache.set(id, cached);
          setSelectedBodyLayerVisible(false);
          setSelectedBody([cached.feature]);
          currentSelectedArea = cached.area;
          scheduleSelectedBodyReveal(id, token);
          rotateToFacadeIfSafe(id, cached.feature.geometry);
          return;
        }
        if (
          currentSelectedId === id &&
          lastCaptureAttemptId === id &&
          lastCaptureAttemptSignature === partsSignature
        ) {
          return;
        }
        lastCaptureAttemptId = id;
        lastCaptureAttemptSignature = partsSignature;

        const geometry = unionFootprintGeometries(found.map((f) => f.geometry));
        if (!geometry) return;

        // Не откатываемся на обрезок, если часть тайлов выгрузилась при движении.
        if (
          currentSelectedId === id &&
          currentSelectedArea > 0 &&
          footprintAreaM2(geometry) < currentSelectedArea * 0.98
        ) {
          return;
        }

        // Высота и индекс у частей одного дома одинаковые; высоту берём
        // максимумом — на случай расхождений между тайлами.
        let height: number | undefined;
        let indexVal: number | undefined;
        for (const f of found) {
          const h = Number(f.properties?.height);
          if (Number.isFinite(h)) height = height === undefined ? h : Math.max(height, h);
          if (indexVal === undefined) {
            const idx = Number(f.properties?.index);
            if (Number.isFinite(idx)) indexVal = idx;
          }
        }

        const unified = {
          type: "Feature" as const,
          id,
          geometry,
          properties: {
            id,
            height: height ?? 9,
          ...(indexVal !== undefined ? { index: indexVal } : {}),
          },
        };
        const feature = unified as unknown as maplibregl.GeoJSONFeature;
        setSelectedBodyLayerVisible(false);
        setSelectedBody([feature]);
        rememberSelectedBody(id, {
          partsSignature,
          feature,
          area: currentSelectedArea,
        });
        scheduleSelectedBodyReveal(id, token);
        rotateToFacadeIfSafe(id, geometry);
      };

      map.on("sourcedata", (e) => {
        if (e.sourceId !== "buildings") return;
        if (selectedId !== null && (e.tile || e.isSourceLoaded)) {
          scheduleSelectedCapture(selectedId, captureToken);
        }
      });

      const deselect = () => {
        cancelSelectedCapture();
        selectedId = null;
        selectedBodyRevealPending = false;
        pendingFacadeBearingId = null;
        routesRef.current = null;
        applySelection();
        updateSelected(null);
        setNonHomeNotice(null);
        setGlow(null);
        setSelectedBody([]);
        setSelectedBodyLayerVisible(true);
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
          const districtId = districtByBuildingRef.current[id];
          if (districtId === undefined) throw new Error("район дома не найден");
          const shardId = id % 16;
          const shardKey = `${districtId}/${shardId}`;
          const shard =
            routesShardCacheRef.current[shardKey] ??
            (routesShardCacheRef.current[shardKey] = fetchGzipJson<
              Record<string, BuildingRoutes>
            >(ROUTES_URL(districtId, shardId)).catch(() => null));
          const data = (await shard)?.[String(id)];
          if (!data) throw new Error(`в шарде ${shardId} нет дома ${id}`);
          const flat: FlatRoutes = {};
          for (const [cat, e] of Object.entries(data.categories)) {
            flat[cat] = { minutes: e.minutes, coordinates: e.geometry.coordinates };
          }
          routesCacheRef.current[id] = flat;
          routesRef.current = flat;
          // В breakdown на тайле есть запасной путь; шард остаётся источником,
          // если отдельный тайловый feature не содержит разбивку.
          if (tileMode) {
            const current = selectedRef.current;
            const breakdown =
              parseBuildingBreakdown(data.breakdown) ??
              (current?.id === id ? current.breakdown : null);
            breakdownCacheRef.current[id] = breakdown;
            if (current?.id === id && breakdown) {
              const next: SelectedBuilding = { ...current, breakdown };
              updateSelected(next);
              rebuildOverlays(next, visibleRef.current);
            }
          }
        } catch (e) {
          console.error(`Маршруты для здания ${id} не загрузились:`, e);
          routesCacheRef.current[id] = null;
          routesRef.current = null;
        }
        if (selectedRef.current?.id === id) {
          rebuildOverlays(selectedRef.current, visibleRef.current);
        }
      };

      const flyToBuilding = (
        center: [number, number],
        id: number,
        token: number,
      ) => {
        // Ждём две отрисовки, чтобы шторка с новым контентом заняла размер,
        // затем центрируем здание в видимой области карты: на мобиле —
        // padding снизу под шторку, на десктопе — padding справа под док.
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            if (!map || token !== captureToken || selectedId !== id) return;
            const sheetEl = document.getElementById("building-sheet");
            const rect = sheetEl?.getBoundingClientRect();
            const desktop = window.matchMedia("(min-width: 768px)").matches;
            const onMoveEnd = () => {
              pendingCaptureMoveEnd = null;
              selectionMovePending = false;
              window.requestAnimationFrame(() => scheduleSelectedCapture(id, token));
            };
            map.flyTo({
              center,
              // Сразу приближаем до зума выбора дома (16.6, режим coarse),
              // чтобы фасады с окнами были видны сразу после клика.
              zoom: Math.max(map.getZoom(), FACADE_SELECT_ZOOM),
              speed: 1.1,
              curve: 1.3,
              padding: desktop
                ? { right: (rect?.width ?? 0) + 24, bottom: 0 }
                : { bottom: (rect?.height ?? 0) + 24 },
            });
            if (map.isMoving()) {
              pendingCaptureMoveEnd = onMoveEnd;
              map.once("moveend", onMoveEnd);
            } else {
              selectionMovePending = false;
              requestAnimationFrame(() => scheduleSelectedCapture(id, token));
            }
          });
        });
      };

      const findBuildingFeature = (id: number): maplibregl.GeoJSONFeature | undefined => {
        if (!map) return undefined;
        const matches = (feature: maplibregl.GeoJSONFeature) =>
          Number(feature.id) === id || Number(feature.properties?.id) === id;
        try {
          return (
            map
              .querySourceFeatures("buildings", { sourceLayer: "buildings" })
              .find(matches) ??
            map.queryRenderedFeatures({ layers: ["buildings"] }).find(matches)
          );
        } catch {
          return undefined;
        }
      };

      const districtForPoint = (center: [number, number]) =>
        districts.find(
          ({ bbox }) =>
            center[0] >= bbox[0] &&
            center[0] <= bbox[2] &&
            center[1] >= bbox[1] &&
            center[1] <= bbox[3],
        );

      // Общая логика выбора: клик по зданию и открытие по ?building=<osm_id>.
      const selectById = (
        id: number,
        clickedFeature?: maplibregl.GeoJSONFeature,
        clickedPoint?: [number, number],
      ) => {
        const existingMeta = metaById[id];
        const feature = clickedFeature ?? findBuildingFeature(id);
        if (!feature) {
          const center = existingMeta?.center;
          if (!center || !map) {
            setDeepLinkError("Дом не найден. Возможно, ссылка устарела — найдите адрес на карте.");
            return;
          }
          const checkAfterMove = () => {
            if (!map) return;
            map.once("idle", () => {
              if (cancelled) return;
              const loaded = findBuildingFeature(id);
              if (loaded) selectById(id, loaded);
              else {
                setDeepLinkError(
                  "Не удалось загрузить здание. Проверьте соединение и повторите попытку.",
                );
              }
            });
          };
          if (map.isMoving()) map.once("moveend", checkAfterMove);
          else checkAfterMove();
          map.flyTo({
            center,
            zoom: Math.max(map.getZoom(), FACADE_SELECT_ZOOM),
            speed: 1.1,
            curve: 1.3,
          });
          return;
        }

        const properties = feature.properties as unknown as BuildingProps;
        const center =
          existingMeta?.center ?? geometryCenter(feature.geometry) ?? null;
        const rawIsHome = properties.is_home ?? existingMeta?.isHome;
        const isHome =
          rawIsHome === true ||
          rawIsHome === 1 ||
          rawIsHome === "1" ||
          rawIsHome === "true";
        if (!isHome) {
          if (selectedId !== null) deselect();
          const point = clickedPoint ?? center;
          if (point) {
            setNotice(null);
            setNonHomeNotice({ point, status: "idle" });
          } else {
            showNotice(
              "Нежилое здание. Индекс окружения считается только для жилых домов",
            );
          }
          return;
        }
        setNonHomeNotice(null);
        if (!center) {
          setDeepLinkError("Не удалось определить положение здания.");
          return;
        }
        const numeric = (value: unknown): number | null => {
          if (value === null || value === undefined || value === "") return null;
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : null;
        };
        const meta: BuildingMeta = {
          ...existingMeta,
          id,
          center,
          address:
            typeof properties.address === "string"
              ? properties.address
              : existingMeta?.address ?? null,
          index: numeric(properties.index) ?? existingMeta?.index ?? null,
          year: numeric(properties.year) ?? existingMeta?.year ?? null,
          isHome: true,
        };
        metaById[id] = meta;
        buildingCenters[id] = center;
        const district = districtForPoint(center);
        if (!district) {
          setDeepLinkError("Не удалось определить район здания.");
          return;
        }
        districtByBuildingRef.current[id] = district.id;

        const token = cancelSelectedCapture();
        selectionMovePending = true;
        selectedId = id;
        selectedBodyRevealPending = true;
        setDeepLinkError(null);
        applySelection(false);
        setSelectedBodyLayerVisible(false);

        const breakdown =
          breakdownCacheRef.current[id] ??
          parseBuildingBreakdown(properties.breakdown);
        if (breakdown) breakdownCacheRef.current[id] = breakdown;
        const next: SelectedBuilding = {
          id,
          index: meta.index ?? null,
          address: meta.address,
          year: meta.year ?? null,
          breakdown,
        };
        updateSelected(next);
        setUrlBuilding(id);
        routesRef.current = routesCacheRef.current[id] ?? null;
        rebuildOverlays(next, visibleRef.current);
        void loadRoutes(id);

        setGlow(center);
        if (currentSelectedId !== id) {
          setSelectedBody([]);
        }
        pendingFacadeBearingId = id;
        flyToBuilding(center, id, token);
      };
      selectRef.current = selectById;
      findNearestHomeRef.current = async (point: [number, number]) => {
        let index = searchIndexData;
        if (!index) {
          setNonHomeNotice((curr) =>
            curr ? { ...curr, status: "loading" } : null,
          );
          try {
            index = await ensureSearchIndex();
          } catch (error) {
            console.error("Индекс для поиска ближайшего дома не загрузился:", error);
            if (!cancelled) {
              setNonHomeNotice((curr) =>
                curr ? { ...curr, status: "error" } : null,
              );
            }
            return;
          }
        }
        if (cancelled) return;
        let bestId: number | null = null;
        let bestDist = Infinity;
        for (let i = 0; i < index.ids.length; i++) {
          const d = haversineM([index.lon[i], index.lat[i]], point);
          if (d < bestDist) {
            bestDist = d;
            bestId = index.ids[i];
          }
        }
        if (bestId === null || bestDist > 200) {
          setNonHomeNotice((curr) =>
            curr ? { ...curr, status: "far" } : null,
          );
          return;
        }
        setNonHomeNotice(null);
        selectById(bestId);
      };

      map.on("load", () => {
        refreshBasemapDecorations();
        applyThemeRef.current?.(themeRef.current);
        applyVisibilityRef.current?.(visibleRef.current);
        setIsoSourceRef.current?.(isoGeomRef.current);
        rebuildLabels();
        // Публичная ссылка «Отправить клиенту»: ?building=<osm_id>
        const param = new URLSearchParams(window.location.search).get("building");
        if (param !== null) {
          const id = Number(param);
          if (Number.isInteger(id)) {
            void loadSearchIndex().then(() => {
              if (cancelled) return;
              if (metaById[id]) {
                selectById(id);
              } else {
                setDeepLinkError(
                  "Дом не найден. Возможно, ссылка устарела — выберите дом на карте или найдите по адресу.",
                );
              }
            });
          } else {
            setDeepLinkError(
              "Дом не найден. Возможно, ссылка устарела — выберите дом на карте или найдите по адресу.",
            );
          }
        }
      });

      const buildingHitLayers = () => {
        const layers = ["buildings"];
        for (const id of [
          "buildings-roof",
          "buildings-selected-roof-edge",
          "buildings-selected-roof",
          "buildings-selected-body",
        ]) {
          if (map?.getLayer(id) && map.getLayoutProperty(id, "visibility") !== "none") {
            layers.unshift(id);
          }
        }
        return layers;
      };

      map.on("click", (e) => {
        if (!map || !map.getLayer("buildings")) return;
        const feats = map.queryRenderedFeatures(e.point, { layers: buildingHitLayers() });
        const rawId = feats.length ? (feats[0].id ?? feats[0].properties?.id) : null;
        // В тайлах id может прийти строкой — приводим к числу (brief B2).
        const id =
          typeof rawId === "string"
            ? Number(rawId)
            : typeof rawId === "number"
              ? rawId
              : null;

        if (id === null || !Number.isFinite(id) || id === selectedId) {
          deselect();
          return;
        }

        selectById(id, feats[0], [e.lngLat.lng, e.lngLat.lat]);
      });

      map.on("mousemove", (e) => {
        if (!map || !map.getLayer("buildings")) return;
        const feats = map.queryRenderedFeatures(e.point, { layers: buildingHitLayers() });
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
      <div ref={containerRef} className="map-gradient map-grade h-full w-full" />
      <div className="map-haze pointer-events-none absolute inset-x-0 top-0" aria-hidden />
      <div className="map-vignette pointer-events-none absolute inset-0" aria-hidden />
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
        onSearch={() => void loadSearchIndexRef.current?.()}
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
      {nonHomeNotice && (
        <div
          className="absolute bottom-6 left-1/2 z-20 w-[min(92vw,420px)] -translate-x-1/2 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow-xl dark:bg-amber-950/85 dark:text-amber-200"
          role="status"
        >
          <div>
            Нежилое здание. Индекс окружения считается только для жилых домов
          </div>
          {nonHomeNotice.status === "far" ? (
            <div className="mt-2 text-xs font-semibold text-amber-900 dark:text-amber-100">
              Рядом нет жилых домов
            </div>
          ) : (
            <button
              type="button"
              disabled={
                nonHomeNotice.status === "loading" ||
                nonHomeNotice.status === "error"
              }
              onClick={() => void findNearestHomeRef.current?.(nonHomeNotice.point)}
              className="mt-2.5 inline-flex items-center justify-center rounded-xl bg-amber-800 px-3.5 py-1.5 text-xs font-semibold text-amber-50 shadow-sm transition hover:bg-amber-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-amber-200 dark:text-amber-950 dark:hover:bg-amber-100"
            >
              {nonHomeNotice.status === "loading"
                ? "ищу…"
                : "Ближайший жилой дом"}
            </button>
          )}
        </div>
      )}
      {notice && (
        <div
          className="absolute bottom-6 left-1/2 z-20 w-[min(92vw,420px)] -translate-x-1/2 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow-xl dark:bg-amber-950/80 dark:text-amber-200"
          role="status"
        >
          {notice}
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
