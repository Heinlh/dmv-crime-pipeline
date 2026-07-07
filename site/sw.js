/* Service worker for DMV Crime Watch.
 *
 * Strategy: network-first with cache fallback for same-origin GETs.
 * The site updates daily (both data and, sometimes, code), so the
 * network copy is always preferred; the cache only serves when the
 * network is unavailable, giving a read-only offline experience with
 * the last data the visitor saw. Cross-origin requests (tiles, CDN
 * libraries, fonts) pass through untouched.
 *
 * All paths are relative so the worker scopes correctly under
 * GitHub Pages project hosting (/dmv-crime-pipeline/).
 */

const CACHE = "dmvcw-v1";

const PRECACHE = [
  "./",
  "index.html",
  "trends.html",
  "events.html",
  "daily.html",
  "alerts.html",
  "about.html",
  "contact.html",
  "privacy.html",
  "css/style.css",
  "js/common.js",
  "js/home.js",
  "js/trends.js",
  "js/events.js",
  "js/daily.js",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // addAll would reject the whole install on one 404; fetch each
      // path individually so a missing file cannot brick the worker
      .then((cache) => Promise.allSettled(PRECACHE.map((p) => cache.add(p))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: true }))
  );
});
