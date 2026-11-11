"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const BUILDINGS_URL = "/data/presnya_buildings.geojson";

// Воркер maplibre отдаём статикой из public/ — bundlers не резолвят
// его динамический URL (Turbopack и webpack одинаково).
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

export default function Map() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          buildings: { type: "geojson", data: BUILDINGS_URL },
        },
        layers: [
          {
            id: "buildings",
            type: "fill-extrusion",
            source: "buildings",
            paint: {
              "fill-extrusion-color": [
                "step",
                ["get", "index"],
                "#dd9c93",
                5,
                "#e9e3d6",
                7,
                "#aad3b0",
              ],
              "fill-extrusion-height": ["coalesce", ["get", "height"], 9],
              "fill-extrusion-base": 0,
              "fill-extrusion-opacity": 0.95,
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

    return () => map.remove();
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="map-gradient h-full w-full" />
      <div className="absolute bottom-2 right-3 max-w-md text-right text-[11px] leading-tight text-slate-500">
        © OpenStreetMap contributors, ODbL · Индекс окружения — справочная
        информация по открытым данным, не является замером или оценкой стоимости
      </div>
    </div>
  );
}
