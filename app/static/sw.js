// Minimal service worker: cache-first for static assets (fast repeat loads),
// network-first for page navigations (stock/prices/cart change constantly,
// so we never want to silently serve a stale page) with an offline fallback.
// Bump CACHE_NAME on any future change here to invalidate old caches.
const CACHE_NAME = "jjg-static-v1";
const PRECACHE_URLS = [
  "/static/css/style.css",
  "/static/js/cart.js",
  "/static/icons/favicon.png",
  "/static/icons/logo-icon.png",
  "/static/manifest.json",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/static/offline.html")));
  }
});
