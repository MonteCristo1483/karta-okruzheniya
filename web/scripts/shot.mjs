#!/usr/bin/env node
// Скриншоты и замер кадров карты в заданном DPR.
// Использование:
//   node scripts/shot.mjs <имя.png> <dpr> <ширина> <высота> [--perf] [--headless]
// Примеры:
//   node scripts/shot.mjs shots/before-1290-dpr1.png 1 1290 900 --perf
//   node scripts/shot.mjs shots/before-1290-dpr2.png 2 1290 900
//
// Готовит Pages-раскладку (dist → .shot/karta-okruzheniya), поднимает
// статик-сервер, снимает скриншот. С --perf сначала меряет простой,
// затем 200 кадров при непрерывном зуме колесом (1.5× и обратно),
// печатает медиану, p95 и число кадров >33 мс.
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const DIST = join(WEB_ROOT, "dist");
const ROOT = join(WEB_ROOT, ".shot");
const SITE = join(ROOT, "karta-okruzheniya");
const PORT = Number(process.env.SHOT_PORT ?? 4199);
const BASE_URL = `http://127.0.0.1:${PORT}/karta-okruzheniya/`;

const [outArg, dprArg, widthArg, heightArg, ...flags] = process.argv.slice(2);
if (!outArg || !dprArg || !widthArg || !heightArg) {
  console.error("usage: node scripts/shot.mjs <out.png> <dpr> <width> <height> [--perf] [--headless]");
  process.exit(1);
}
const out = resolve(WEB_ROOT, outArg);
const dpr = Number(dprArg);
const width = Number(widthArg);
const height = Number(heightArg);
const perf = flags.includes("--perf");
const headless = flags.includes("--headless");

async function waitForServer() {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(BASE_URL)).ok) return;
    } catch {
      // сервер ещё стартует
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("сервер не поднялся");
}

if (!existsSync(join(DIST, "index.html"))) {
  console.error("Нет web/dist — сначала `npm run build`.");
  process.exit(1);
}

rmSync(SITE, { recursive: true, force: true });
mkdirSync(ROOT, { recursive: true });
cpSync(DIST, SITE, { recursive: true });

const server = spawn(
  process.execPath,
  [join(WEB_ROOT, "scripts", "serve-test.mjs"), ROOT, String(PORT)],
  { cwd: WEB_ROOT, stdio: ["ignore", "inherit", "inherit"] },
);

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({
    headless,
    args: ["--ignore-gpu-blocklist", "--enable-gpu-rasterization"],
  });
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: dpr,
  });
  const page = await context.newPage();
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await page.waitForSelector("canvas.maplibregl-canvas", { timeout: 20_000 });
  await page.waitForTimeout(5_000);

  if (perf) {
    const attrs = await page.evaluate(() => {
      const canvas = document.querySelector("canvas.maplibregl-canvas");
      const gl = canvas?.getContext("webgl2");
      return gl?.getContextAttributes() ?? null;
    });
    console.log(
      `context: antialias=${attrs?.antialias} dpr=${dpr} canvas=${await page.evaluate(
        () => `${document.querySelector("canvas.maplibregl-canvas")?.width}x${document.querySelector("canvas.maplibregl-canvas")?.height}`,
      )}`,
    );
    const measure = async (label, zooming) => {
      await page.evaluate(() => {
        window.__perfFrames = [];
        let last = performance.now();
        const tick = (t) => {
          window.__perfFrames.push(t - last);
          last = t;
          if (window.__perfFrames.length < 400) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      if (zooming) {
        await page.mouse.move(width / 2, height / 2);
        for (let i = 0; i < 24; i++) {
          await page.mouse.wheel(0, i < 12 ? -120 : 120);
          await page.waitForTimeout(70);
        }
      } else {
        await page.waitForTimeout(3_200);
      }
      const frames = await page.evaluate(() => window.__perfFrames.slice(0, 200));
      const sorted = [...frames].sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      const p95 = sorted[Math.floor(sorted.length * 0.95)];
      const over = frames.filter((f) => f > 33).length;
      const round = (n) => Math.round(n * 10) / 10;
      console.log(
        `${label}: median=${round(median)}ms p95=${round(p95)}ms >33ms=${over}/${frames.length}`,
      );
    };
    await measure("idle", false);
    await measure("zoom 1.5x", true);
  }

  mkdirSync(dirname(out), { recursive: true });
  await page.screenshot({ path: out });
  console.log(`saved ${out} (${width}x${height} @${dpr}x)`);
} finally {
  await browser?.close();
  server.kill();
  rmSync(SITE, { recursive: true, force: true });
}
