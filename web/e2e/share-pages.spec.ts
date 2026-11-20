import { test, expect } from "@playwright/test";

// Страницы /r/<id>/ генерируются в dist после сборки (generate-building-pages).
// Проверяем три реальных дома из данных; 64091236 — из брифа.
const PAGES = [
  { id: 64091236, address: "Тверской бульвар, 3/1" },
  { id: 64091280, address: "Малая Никитская улица, 14 с1" },
  { id: 64091298, address: "Скарятинский переулок, 5" },
];

test("/r/<id>/ отдаёт 200 и содержит адрес (3 дома)", async ({ request }) => {
  for (const { id, address } of PAGES) {
    // Путь без ведущего слэша: baseURL уже содержит /karta-okruzheniya/.
    const response = await request.get(`r/${id}/`);
    expect(response.status(), `GET /r/${id}/`).toBe(200);
    expect(await response.text(), `адрес на /r/${id}/`).toContain(address);
  }
});
