"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry, Position } from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
import BuildingSheet from "./building-sheet";
import type { BuildingBreakdown, SelectedBuilding } from "./building-sheet";
import LayersPanel, { DEFAULT_VISIBILITY } from "./layers-panel";
import type { Visibility } from "./layers-panel";
import { geometryCenter, haversineM, nearestPosition } from "./geo-utils";
import { initialTheme, THEME_KEY } from "./theme";
import type { Theme } from "./theme";

const BUILDINGS_URL = "/data/presnya_buildings.geojson";
const POI_URL = "/data/presnya_poi.geojson";
const REDZONES_URL = "/data/presnya_redzones.geojson";

// Воркер maplibre отдаём статикой из public/ — bundlers не резолвят
// его динамический URL (Turbopack и webpack одинаково).
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

// Палитры карты по темам (мягкие акценты, не неон — SPEC).
const MAP_THEMES = {
  light: {
    low: "#dd9c93",
    mid: "#e9e3d6",
    high: "#aad3b0",
    selected: "#7fb069",
    dim: "#b3bcc0",
    halo: "#4f9d64",
    outlineCore: "#3d8b53",
    redzone: "#d98f86",
    route: "#5fa86b",
    poiGreen: "#059669",
    poiMetro: "#e11d48",
    pill: "bg-white/90 text-slate-700",
    redPill: "bg-white/95 text-red-700",
    redIcon: "#b91c1c",
  },
  dark: {
    low: "#a06a63",
    mid: "#3d4653",
    high: "#5d8a6a",
    selected: "#6a9d76",
    dim: "#343b46",
    halo: "#7fb98a",
    outlineCore: "#8fce9a",
    redzone: "#8f5b55",
    route: "#6a9d76",
    poiGreen: "#7fb98a",
    poiMetro: "#ef8fa4",
    pill: "bg-slate-800/90 text-slate-200",
    redPill: "bg-slate-800/95 text-red-300",
    redIcon: "#f87171",
  },
} as const;

type Palette = { [K in keyof (typeof MAP_THEMES)["light"]]: string };

const buildingColorExpr = (p: Palette) =>
  [
    "case",
    ["to-boolean", ["feature-state", "selected"]],
    p.selected,
    ["to-boolean", ["feature-state", "inactive"]],
    p.dim,
    ["step", ["get", "index"], p.low, 5, p.mid, 7, p.high],
  ] as never;

type PoiProps = { type: string; subtype: string | null; name: string | null };
type RedzoneProps = { kind: string; class: string | null };
type GFeature<P> = { type: "Feature"; geometry: Geometry; properties: P };
type GCollection<P> = { type: "FeatureCollection"; features: GFeature<P>[] };

const POI_LABELS: Record<string, string> = {
  school: "Школа",
  kindergarten: "Сад",
  park: "Парк",
  subway_entrance: "Метро",
  shop: "Магазин",
};

function poiColor(type: string, p: Palette): string {
  return type === "subway_entrance" ? p.poiMetro : p.poiGreen;
}

// POI type -> ключ слоя-чекбокса / ключа бонуса в breakdown
const POI_TO_LAYER: Record<string, keyof Visibility> = {
  school: "schools",
  kindergarten: "kindergartens",
  park: "parks",
  subway_entrance: "metro",
  shop: "shops",
};

const POI_TO_BONUS: Record<string, string> = {
  school: "schools",
  kindergarten: "kindergartens",
  park: "parks",
  subway_entrance: "transport",
  shop: "shops",
};

const BONUS_KEYS = ["transport", "schools", "parks", "kindergartens", "shops"];

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

function makePoiMarkerEl(color: string, text: string, pill: string): HTMLDivElement {
  const el = pillBase(pill);
  const dot = document.createElement("span");
  dot.style.color = color;
  dot.textContent = "●";
  const label = document.createElement("span");
  label.textContent = text;
  el.append(dot, label);
  return el;
}

function makeRedPinEl(text: string, pill: string, iconColor: string): HTMLDivElement {
  const el = pillBase(`${pill} red-pin`);
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

export default function Map() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<SelectedBuilding | null>(null);
  const [visible, setVisible] = useState<Visibility>(DEFAULT_VISIBILITY);
  const [theme, setTheme] = useState<Theme>("light"); // стабильно для SSR/гидрации
  const [mounted, setMounted] = useState(false);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const deselectRef = useRef<(() => void) | null>(null);
  const applyVisibilityRef = useRef<((v: Visibility) => void) | null>(null);
  const applyThemeRef = useRef<((t: Theme) => void) | null>(null);
  const selectedRef = useRef<SelectedBuilding | null>(null);
  const visibleRef = useRef<Visibility>(DEFAULT_VISIBILITY);
  // Реальная тема для карты доступна сразу (карта — клиент-only).
  const themeRef = useRef<Theme>(initialTheme());

  const updateSelected = (b: SelectedBuilding | null) => {
    selectedRef.current = b;
    setSelected(b);
  };

  useEffect(() => {
    visibleRef.current = visible;
    applyVisibilityRef.current?.(visible);
  }, [visible]);

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

      let buildings: GCollection<{ id: number }>;
      let poi: GCollection<PoiProps>;
      let redzones: GCollection<RedzoneProps>;
      try {
        [buildings, poi, redzones] = (await Promise.all(
          [BUILDINGS_URL, POI_URL, REDZONES_URL].map(fetchJson),
        )) as [
          GCollection<{ id: number }>,
          GCollection<PoiProps>,
          GCollection<RedzoneProps>,
        ];
      } catch (e) {
        console.error("Не удалось загрузить данные карты:", e);
        setFatalError(
          "Не удалось загрузить данные карты. Проверьте соединение и обновите страницу.",
        );
        return;
      }
      if (cancelled) return;
      const palette = MAP_THEMES[themeRef.current];

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
          sources: {
            buildings: { type: "geojson", data: buildings, promoteId: "id" },
            redzones: { type: "geojson", data: redzones },
            routes: { type: "geojson", data: routesData },
          },
          layers: [
            {
              id: "redzones-fill",
              type: "fill",
              source: "redzones",
              filter: ["==", ["geometry-type"], "Polygon"],
              paint: { "fill-color": palette.redzone, "fill-opacity": 0.14 },
            },
            {
              id: "redzones-line",
              type: "line",
              source: "redzones",
              filter: ["==", ["geometry-type"], "LineString"],
              paint: {
                "line-color": palette.redzone,
                "line-width": 2.5,
                "line-opacity": 0.7,
              },
            },
            {
              id: "buildings",
              type: "fill-extrusion",
              source: "buildings",
              paint: {
                // fill-extrusion-color поддерживает feature-state,
                // fill-extrusion-opacity — нет, поэтому затемнение через цвет.
                "fill-extrusion-color": buildingColorExpr(palette),
                "fill-extrusion-height": ["coalesce", ["get", "height"], 9],
                "fill-extrusion-base": 0,
                "fill-extrusion-opacity": 0.95,
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
                  0.25,
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
                "line-width": 3,
                "line-opacity": 0.75,
              },
            },
          ],
        },
        center: [37.547, 55.762],
        zoom: 14.3,
        pitch: 60,
        bearing: -18,
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
      map.on("error", (e) => console.error("map error:", e.error ?? e));
      (window as unknown as { __map?: maplibregl.Map }).__map = map;

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
          let text = f.properties.name ?? POI_LABELS[f.properties.type] ?? f.properties.type;
          if (sel && selCenter && nearestByCat[bonusKey] === f) {
            const entry = sel.breakdown?.bonuses[bonusKey];
            if (entry) text = `${text} · ${Math.round(entry.minutes)} мин`;
          }
          poiMarkers.push(
            new maplibregl.Marker({
              element: makePoiMarkerEl(poiColor(f.properties.type, theme), text, theme.pill),
              anchor: "bottom",
            })
              .setLngLat(center)
              .addTo(map),
          );
        }

        if (sel?.breakdown && selCenter) {
          for (const [key, entry] of Object.entries(sel.breakdown.penalties)) {
            if (!entry || entry.penalty >= -0.005) continue;
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
          for (const cat of BONUS_KEYS) {
            const target = nearestPoi(cat, selCenter);
            if (target) {
              routeFeatures.push({
                type: "Feature",
                properties: { category: cat },
                geometry: { type: "LineString", coordinates: [selCenter, target.center] },
              });
            }
          }
        }
        routesData.features = routeFeatures;
        (map.getSource("routes") as maplibregl.GeoJSONSource | null)?.setData(routesData);
      };

      applyVisibilityRef.current = (v: Visibility) => {
        if (!map) return;
        if (map.getLayer("redzones-line")) {
          const rz = v.redzones ? "visible" : "none";
          map.setLayoutProperty("redzones-line", "visibility", rz);
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
        map.setPaintProperty("buildings", "fill-extrusion-color", buildingColorExpr(p));
        map.setPaintProperty("outline-halo", "line-color", p.halo);
        map.setPaintProperty("outline-core", "line-color", p.outlineCore);
        map.setPaintProperty("redzones-fill", "fill-color", p.redzone);
        map.setPaintProperty("redzones-line", "line-color", p.redzone);
        map.setPaintProperty("routes-line", "line-color", p.route);
        rebuildOverlays(selectedRef.current, visibleRef.current);
      };

      let selectedId: number | null = null;
      const allIds = buildings.features.map((f) => f.properties.id);

      map.on("load", () => applyThemeRef.current?.(themeRef.current));

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

      const deselect = () => {
        selectedId = null;
        applySelection();
        updateSelected(null);
        rebuildOverlays(null, visibleRef.current);
        map?.easeTo({ padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: 300 });
      };
      deselectRef.current = deselect;

      map.on("click", (e) => {
        if (!map || !map.getLayer("buildings")) return;
        const feats = map.queryRenderedFeatures(e.point, { layers: ["buildings"] });
        const id = feats.length ? (feats[0].id as number) : null;

        if (id === null || id === selectedId) {
          deselect();
          return;
        }

        selectedId = id;
        applySelection();

        const props = feats[0].properties as {
          index?: number;
          address?: string;
          breakdown?: BuildingBreakdown;
        };
        const next: SelectedBuilding = {
          id,
          index: props.index ?? null,
          address: props.address ?? null,
          breakdown: props.breakdown ?? null,
        };
        updateSelected(next);
        rebuildOverlays(next, visibleRef.current);

        const center = geometryCenter(feats[0].geometry);
        if (center) {
          // Ждём две отрисовки, чтобы шторка с новым контентом заняла высоту,
          // затем центрируем здание в видимой области (padding снизу).
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              if (!map) return;
              const sheetEl = document.getElementById("building-sheet");
              const bottom = (sheetEl?.offsetHeight ?? 0) + 24;
              map.flyTo({
                center,
                zoom: Math.max(map.getZoom(), 15.2),
                speed: 1.1,
                curve: 1.3,
                padding: { bottom },
              });
            });
          });
        }
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
      <BuildingSheet building={selected} onClose={() => deselectRef.current?.()} />
      {fatalError && (
        <div className="absolute inset-x-4 top-1/3 z-20 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-800 shadow-lg dark:bg-red-950/70 dark:text-red-200">
          {fatalError}
        </div>
      )}
      <div className="pointer-events-none absolute bottom-2 right-3 max-w-md text-right text-[11px] leading-tight text-slate-500 dark:text-slate-400">
        © OpenStreetMap contributors, ODbL · Индекс окружения — справочная
        информация по открытым данным, не является замером или оценкой стоимости
      </div>
    </div>
  );
}
