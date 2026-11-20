import { defineConfig, devices } from "@playwright/test";

// baseURL приходит из `scripts/run-e2e.mjs` (локальный сервер) или из env
// в CI. Сайт живёт в подпапке GitHub Pages, поэтому по умолчанию —
// /karta-okruzheniya/.
const port = process.env.E2E_PORT ?? "4173";
const baseURL =
  process.env.E2E_BASE_URL ?? `http://127.0.0.1:${port}/karta-okruzheniya/`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  // Тесты делят один статический сервер — без параллельных воркеров.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL,
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
