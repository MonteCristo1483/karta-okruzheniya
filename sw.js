// Service Worker «Карта окружения».
// Навигации (HTML) и данные (data/*.geojson, data/routes/*.json) —
// network-first: после деплоя пользователь сразу видит новую версию и
// свежие данные, офлайн — из кэша. Хэшированные чанки —
// stale-while-revalidate: открываются мгновенно и при сети, и из кэша.
const CACHE_NAME = "karta-okruzheniya-v3";
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
  return cached || fetched;
}

// HTML — сначала сеть: деплой виден сразу. Офлайн — кэш (PWA).
async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    if (request.mode === "navigate") {
      const page = await cache.match(BASE + "/index.html");
      if (page) return page;
    }
    return Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  // Данные — тоже network-first: файл по тому же URL перезаписывается
  // при деплое, stale-first отдавал старые geojson до следующего захода.
  const isData = url.pathname.startsWith(BASE + "/data/");
  event.respondWith(
    event.request.mode === "navigate" || isData
      ? networkFirst(event.request)
      : staleWhileRevalidate(event.request),
  );
});
