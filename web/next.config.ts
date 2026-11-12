import type { NextConfig } from "next";

// Статический экспорт для GitHub Pages: сайт живёт в подпапке репозитория
// (/karta-okruzheniya), поэтому все ресурсы в коде — относительные пути
// (map.tsx: fetch GeoJSON и setWorkerUrl).
const nextConfig: NextConfig = {
  output: "export",
  distDir: "dist",
  basePath: "/karta-okruzheniya",
};

export default nextConfig;
