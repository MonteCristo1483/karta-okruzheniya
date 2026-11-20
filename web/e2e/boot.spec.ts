import { test, expect } from "@playwright/test";

// Boot-смоук: страница отдаётся, поиск и панель слоёв на месте, данных
// хватило, чтобы не показать fatal-баннер. Карта/WebGL не проверяются.
test("Boot: открывается страница, есть поиск и 7 чипов, нет fatal-баннера", async ({
  page,
}) => {
  const response = await page.goto("./");
  expect(response?.status(), "GET / (главная)").toBe(200);

  await expect(page.getByLabel("Поиск дома по адресу")).toBeVisible();

  const chips = page.getByRole("checkbox");
  await expect(chips).toHaveCount(7);
  for (const label of [
    "Школы",
    "Сады",
    "Парки",
    "Метро",
    "Магазины",
    "Красные зоны",
    "Маршруты",
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }

  // Splash уходит максимум через 2 с + 350 мс фейда; fatal-баннер возможен
  // только после неудачной загрузки данных, то есть после этого момента.
  await page.waitForTimeout(2_500);
  await expect(page.getByText("Не удалось загрузить данные карты")).toHaveCount(
    0,
  );
  await expect(page.getByText("Ошибка загрузки карты")).toHaveCount(0);
});
