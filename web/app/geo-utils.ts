import type { Geometry, Position } from "geojson";

export function haversineM(a: Position, b: Position): number {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad;
  const dLon = (b[0] - a[0]) * rad;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(s));
}

export function eachPosition(geometry: Geometry, fn: (p: Position) => void): void {
  if (geometry.type === "GeometryCollection") return;
  const depth =
    geometry.type === "Point"
      ? 0
      : geometry.type === "LineString"
        ? 1
        : geometry.type === "Polygon"
          ? 2
          : 3; // MultiPolygon: polygons -> rings -> positions
  const walk = (coords: unknown, d: number): void => {
    if (d === 0) {
      fn(coords as Position);
      return;
    }
    for (const child of coords as unknown[]) walk(child, d - 1);
  };
  walk(geometry.coordinates as unknown, depth);
}

export function geometryCenter(geometry: Geometry): [number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let seen = false;
  eachPosition(geometry, ([x, y]) => {
    seen = true;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  });
  return seen ? [(minX + maxX) / 2, (minY + maxY) / 2] : null;
}

export function nearestPosition(
  geometry: Geometry,
  target: [number, number],
): { point: Position; dist: number } | null {
  let best: { point: Position; dist: number } | null = null;
  eachPosition(geometry, (p) => {
    const dist = haversineM(p, target);
    if (!best || dist < best.dist) best = { point: p, dist };
  });
  return best;
}
