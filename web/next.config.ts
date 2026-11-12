import type { NextConfig } from "next";

// Статический экспорт для GitHub Pages: сайт живёт в подпапке репозитория
// (/karta-okruzheniya), поэтому все ресурсы в коде — относительные пути
// (map.tsx: fetch GeoJSON и setWorkerUrl).
// assetPrefix равен basePath: без него Turbopack оставляет часть ссылок на
// чанки абсолютными (/_next/static/...) — они 404 в подпапке Pages.
const nextConfig: NextConfig = {
  output: "export",
  distDir: "dist",
  basePath: "/karta-okruzheniya",
  assetPrefix: "/karta-okruzheniya",
  trailingSlash: true,
};

export default nextConfig;
