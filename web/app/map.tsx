"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { FeatureCollection, Geometry } from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
import BuildingSheet from "./building-sheet";
import type { BuildingBreakdown, SelectedBuilding } from "./building-sheet";

const BUILDINGS_URL = "/data/presnya_buildings.geojson";

// Воркер maplibre отдаём статикой из public/ — bundlers не резолвят
// его динамический URL (Turbopack и webpack одинаково).
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

const COLOR_LOW = "#dd9c93";
const COLOR_MID = "#e9e3d6";
const COLOR_HIGH = "#aad3b0";
const COLOR_SELECTED = "#7fb069";
const COLOR_DIM = "#b3bcc0";
const COLOR_OUTLINE = "#4f9d64";

function footprintCenter(geometry: Geometry): [number, number] | null {
  const rings =
    geometry.type === "Polygon"
      ? [geometry.coordinates[0]]
      : geometry.type === "MultiPolygon"
        ? geometry.coordinates.map((polygon) => polygon[0])
        : null;
  if (!rings) return null;
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
  return [(minX + maxX) / 2, (minY + maxY) / 2];
}

export default function Map() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<SelectedBuilding | null>(null);
  const deselectRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    let cancelled = false;
    let map: maplibregl.Map | null = null;

    const init = async () => {
      const data = (await (await fetch(BUILDINGS_URL)).json()) as FeatureCollection;
      if (cancelled) return;
      const allIds = data.features.map((f) => f.properties?.id as number);
      let selectedId: number | null = null;

      map = new maplibregl.Map({
        container,
        style: {
          version: 8,
          sources: {
            buildings: { type: "geojson", data, promoteId: "id" },
          },
          layers: [
            {
              id: "buildings",
              type: "fill-extrusion",
              source: "buildings",
              paint: {
                // fill-extrusion-color поддерживает feature-state,
                // fill-extrusion-opacity — нет, поэтому затемнение через цвет.
                "fill-extrusion-color": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  COLOR_SELECTED,
                  ["to-boolean", ["feature-state", "inactive"]],
                  COLOR_DIM,
                  [
                    "step",
                    ["get", "index"],
                    COLOR_LOW,
                    5,
                    COLOR_MID,
                    7,
                    COLOR_HIGH,
                  ],
                ],
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
                "line-color": COLOR_OUTLINE,
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
                "line-color": "#3d8b53",
                "line-width": 3,
                "line-opacity": [
                  "case",
                  ["to-boolean", ["feature-state", "selected"]],
                  0.95,
                  0,
                ],
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

      map.addControl(new maplibregl.NavigationControl(), "top-right");

      // Для проверок playwright: доступ к экземпляру карты.
      (window as unknown as { __map?: maplibregl.Map }).__map = map;

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
        setSelected(null);
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
        setSelected({
          id,
          index: props.index ?? null,
          address: props.address ?? null,
          breakdown: props.breakdown ?? null,
        });

        const center = footprintCenter(feats[0].geometry);
        if (center) {
          map.flyTo({
            center,
            zoom: Math.max(map.getZoom(), 15.2),
            speed: 1.1,
            curve: 1.3,
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
      <BuildingSheet building={selected} onClose={() => deselectRef.current?.()} />
      <div className="absolute bottom-2 right-3 max-w-md text-right text-[11px] leading-tight text-slate-500">
        © OpenStreetMap contributors, ODbL · Индекс окружения — справочная
        информация по открытым данным, не является замером или оценкой стоимости
      </div>
    </div>
  );
}
