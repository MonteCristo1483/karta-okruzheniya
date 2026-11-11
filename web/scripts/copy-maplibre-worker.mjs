import { cpSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = dirname(dirname(fileURLToPath(import.meta.url)));
cpSync(
  join(webRoot, "node_modules", "maplibre-gl", "dist", "maplibre-gl-worker.mjs"),
  join(webRoot, "public", "maplibre-gl-worker.mjs"),
);
console.log("maplibre-gl-worker.mjs скопирован в public/");
