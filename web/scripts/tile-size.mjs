#!/usr/bin/env node
// Dev-инструмент: веса тайлов в PMTiles.
//
// Зачем: прод тормозил из-за тайлов в мегабайты на обзорных зумах. Здесь
// видно, сколько реально весит тайл на каждом зуме для точки Москвы, плюс
// minZoom/maxZoom/numAddressedTiles из заголовка архива.
//
// Использование (из корня репозитория, зависимости — web/node_modules):
//   node web/scripts/tile-size.mjs data/buildings.pmtiles
//   node web/scripts/tile-size.mjs data/basemap.pmtiles --lon 37.62 --lat 55.75
//
// Ничего не пишет на диск и не ходит в сеть: читает файл через интерфейс,
// который ждёт pmtiles. Только чтение — безопасно для артефактов.

import fs from "node:fs";
import path from "node:path";
import { PMTiles } from "pmtiles";

const args = process.argv.slice(2);
if (args.length === 0 || args[0].startsWith("-")) {
  console.error(
    "usage: node web/scripts/tile-size.mjs <file.pmtiles> [--lon 37.62] [--lat 55.75] [--min-zoom 8] [--max-zoom 16]",
  );
  process.exit(1);
}

const option = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] !== undefined ? args[index + 1] : fallback;
};

const file = path.resolve(args[0]);
const lon = Number(option("lon", 37.62));
const lat = Number(option("lat", 55.75));
const minZoom = Number(option("min-zoom", 8));
const maxZoom = Number(option("max-zoom", 16));

if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
  console.error("--lon/--lat должны быть числами");
  process.exit(1);
}
if (!fs.existsSync(file)) {
  console.error(`нет файла: ${file}`);
  process.exit(1);
}

const fd = fs.openSync(file, "r");
const source = {
  getKey() {
    return file;
  },
  async getBytes(offset, length) {
    const buf = Buffer.alloc(length);
    const read = fs.readSync(fd, buf, 0, length, offset);
    return { data: buf.buffer.slice(buf.byteOffset, buf.byteOffset + read) };
  },
};

// Web-Mercator: тайл, в который попадает точка, на каждом зуме.
function tileOf(z) {
  const n = 2 ** z;
  const x = Math.floor(((lon + 180) / 360) * n);
  const rad = (lat * Math.PI) / 180;
  const y = Math.floor(
    ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n,
  );
  return {
    x: Math.min(n - 1, Math.max(0, x)),
    y: Math.min(n - 1, Math.max(0, y)),
  };
}

const kb = (bytes) => `${(bytes / 1024).toFixed(0)} KB`;
const mb = (bytes) => `${(bytes / (1 << 20)).toFixed(2)} MB`;

try {
  const pmtiles = new PMTiles(source);
  const header = await pmtiles.getHeader();
  const stat = fs.statSync(file);

  console.log(`file          : ${args[0]}`);
  console.log(`size          : ${mb(stat.size)} (${stat.size.toLocaleString("ru-RU")} B)`);
  console.log(`minZoom       : ${header.minZoom}`);
  console.log(`maxZoom       : ${header.maxZoom}`);
  console.log(`numTiles      : ${header.numTiles ?? "n/a"}`);
  console.log(`addressedTiles: ${header.numAddressedTiles}`);
  console.log(`point         : ${lon}, ${lat}`);
  console.log("");
  console.log("zoom | tile          | size");
  console.log("-----+---------------+----------");

  for (let z = minZoom; z <= maxZoom; z++) {
    const { x, y } = tileOf(z);
    const tile = await pmtiles.getZxy(z, x, y);
    const label = `z${z}`.padEnd(4);
    const where = `${x}/${y}`.padEnd(13);
    const size = tile && tile.data ? kb(tile.data.byteLength) : "empty";
    console.log(`${label} | ${where} | ${size}`);
  }
} finally {
  fs.closeSync(fd);
}
