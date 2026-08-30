// Minimalny service worker — wymagany przez Chrome/Android, aby aplikację
// można było zainstalować na ekranie głównym ("Add to Home Screen").
// Dane z /api/analyze NIE są cache'owane, bo muszą być zawsze świeże.
//
// WAŻNE: strategia "network-first" (nie "cache-first"). Wcześniejsza wersja
// serwowała cache w pierwszej kolejności, więc zaktualizowany app.js/index.html
// wdrożony na serwerze mógł nigdy nie dotrzeć do przeglądarki, dopóki cache
// nie wygasł ręcznie. Teraz zawsze próbujemy najpierw sieci — cache służy
// wyłącznie jako awaryjny fallback, gdy urządzenie jest offline.

const CACHE_NAME = "analiza-dzialki-v3";
const APP_SHELL = [
  "/",
  "/static/app.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Nigdy nie cache'uj wywołań do API — dane muszą być zawsze aktualne.
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
