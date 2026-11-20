import { test, expect } from "@playwright/test";

// Тема: клик по кнопке меняет data-theme на dark и выбор переживает
// перезагрузку (localStorage karta-theme + антфлеш-скрипт в layout).
test("Тема: переключается на dark и сохраняется после перезагрузки", async ({
  page,
}) => {
  await page.goto("./");

  const toggle = page.getByRole("button", { name: "Переключить тему" });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toBeVisible();
});
