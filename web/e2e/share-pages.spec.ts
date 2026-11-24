import { test, expect } from "@playwright/test";

// Страниц на дом больше нет: /r/ — одна страница приложения, которая читает
// ?b=<id> и уводит на карту. Старые ссылки /r/<id>/ ловит public/404.html
// (GitHub Pages отдаёт его на неизвестный путь; serve-test.mjs имитирует).
const ID = 49553156;

// После редиректа карта открывает дом по ?building=<id>; карточку на этой
// странице не рисуем — проверяем адрес и что карта вообще поднялась.
test("Поделиться: /r/?b=<id> уводит на карту с ?building=<id>", async ({
  page,
}) => {
  await page.goto(`r/?b=${ID}`);
  await page.waitForURL((url) => url.searchParams.get("building") === String(ID));

  const url = new URL(page.url());
  expect(url.pathname).toMatch(/\/$/);
  expect(url.searchParams.get("building")).toBe(String(ID));
  await expect(page.getByLabel("Поиск дома по адресу")).toBeVisible();
});

test("Поделиться: старая ссылка /r/<id>/ открывает тот же дом", async ({
  page,
}) => {
  await page.goto(`r/${ID}/`);
  await page.waitForURL((url) => url.searchParams.get("building") === String(ID));

  const url = new URL(page.url());
  expect(url.pathname).toMatch(/\/$/);
  expect(url.searchParams.get("building")).toBe(String(ID));
  await expect(page.getByLabel("Поиск дома по адресу")).toBeVisible();
});

test("Поделиться: /r/ без параметра — «Ссылка неполная» и ссылка на карту", async ({
  page,
}) => {
  await page.goto("r/");

  await expect(page.getByText("Ссылка неполная")).toBeVisible();
  const link = page.getByRole("link", { name: "Открыть карту" });
  await expect(link).toHaveAttribute("href", /\/$/);
  await link.click();
  await expect(page.getByLabel("Поиск дома по адресу")).toBeVisible();
});
