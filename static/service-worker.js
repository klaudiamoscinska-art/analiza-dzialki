// Minimalny service worker — wymagany przez Chrome/Android, aby aplikację
// można było zainstalować na ekranie głównym ("Add to Home Screen").
// Dane z /api/analyze NIE są cache'owane, bo muszą być zawsze świeże.
//
// WAŻNE: strategia "network-first" (nie "cache-first"). Wcześniejsza wersja
// serwowała cache w pierwszej kolejności, więc zaktualizowany app.js/index.html
// wdrożony na serwerze mógł nigdy nie dotrzeć do przeglądarki, dopóki cache
// nie wygasł ręcznie. Teraz zawsze próbujemy najpierw sieci — cache służy
// wyłącznie jako awaryjny fallback, gdy urządzenie jest offline.
//
// Potwierdzony na żywo drugi poziom cache'owania: `fetch()` bez opcji
// używa domyślnego trybu HTTP-cache przeglądarki, a `/static/` nie ma
// jawnego nagłówka Cache-Control (patrz main.py) — bez `cache: "no-store"`
// "sieć najpierw" może i tak cicho oddać stary plik z dysku, zwłaszcza
// w appce dodanej do ekranu głównego (iOS Safari rzadziej sprawdza
// aktualizację service workera niż zwykła karta). Stąd `no-store` niżej.
const CACHE_NAME = "analiza-dzialki-v21";
const APP_SHELL = [
  "/",
  "/static/app.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(APP_SHELL.map((url) => new Request(url, { cache: "reload" })))
    )
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
  // new URL() rzuca "The string did not match the expected pattern" dla
  // pewnych żądań na iOS Safari (potwierdzone na żywo przez Klaudię —
  // ten sam surowy, angielski komunikat przeglądarki, niepochodzący z
  // żadnego kodu tej appki, wyciekł do UI) — dodane 2026-09-04. Ten
  // konkretny fetch event nie jest wart przechwytywania/cache'owania,
  // więc zamiast crashować cały handler, po prostu go pomijamy i
  // pozwalamy przeglądarce obsłużyć go normalnie, tak jakby service
  // workera tu nie było.
  let url;
  try {
    url = new URL(event.request.url);
  } catch (err) {
    return;
  }

  // Nigdy nie cache'uj wywołań do API — dane muszą być zawsze aktualne.
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  event.respondWith(
    fetch(event.request, { cache: "no-store" })
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
