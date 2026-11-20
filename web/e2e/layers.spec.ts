import { test, expect } from "@playwright/test";

// Слои: чип «Маршруты» — обычный чекбокс в label. Клик по видимому тексту
// переключает checked и обратно; остальные шесть чипов остаются на месте.
test("Слои: чип «Маршруты» переключает checked и обратно", async ({ page }) => {
  await page.goto("./");

  const routes = page.getByRole("checkbox", { name: "Маршруты" });
  await expect(routes).not.toBeChecked();

  await page.getByText("Маршруты", { exact: true }).click();
  await expect(routes).toBeChecked();

  await page.getByText("Маршруты", { exact: true }).click();
  await expect(routes).not.toBeChecked();

  await expect(page.getByRole("checkbox")).toHaveCount(7);
});
