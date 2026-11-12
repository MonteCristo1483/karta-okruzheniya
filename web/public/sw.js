// Service Worker «Карта окружения»: stale-while-revalidate.
// Сначала отвечаем из кэша, параллельно обновляем ресурс в фоне —
// карта открывается мгновенно и при сети, и из кэша (PWA).
const CACHE_NAME = "karta-okruzheniya-v1";
const BASE = "/karta-okruzheniya";

// Стабильные файлы, кэшируемые при установке. JS/CSS-чанки hashed —
// их подхватывает runtime-стратегия ниже.
const PRECACHE = [
  BASE + "/",
  BASE + "/index.html",
  BASE + "/manifest.json",
  BASE + "/icon.svg",
  BASE + "/maplibre-gl-worker.mjs",
  BASE + "/data/presnya_buildings.geojson",
  BASE + "/data/presnya_poi.geojson",
  BASE + "/data/presnya_redzones.geojson",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        // По одному: один недоступный файл не должен ронять весь install.
        Promise.allSettled(PRECACHE.map((url) => cache.add(url))),
      )
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const fetched = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  // Навигация (?building=id) → при отсутствии сети отдаём index.html.
  if (!cached && request.mode === "navigate") {
    const page = await cache.match(BASE + "/index.html");
    if (page) return page;
  }
  return cached || fetched;
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  event.respondWith(staleWhileRevalidate(event.request));
});
