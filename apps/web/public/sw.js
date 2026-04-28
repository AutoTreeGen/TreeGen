/* AutoTreeGen service worker (Phase 4.6, ADR-0041).
 *
 * Минимальный кэш static assets (Next.js _next/static, шрифты, иконки).
 * API responses НЕ кэшируются — иначе пользователь увидит stale tree
 * data, а consequence в provenance-first приложении хуже, чем offline.
 *
 * Стратегии:
 *
 * - install: pre-cache hardcoded core (offline shell — minimal HTML).
 * - fetch:
 *   - same-origin GET к /_next/static/* или к статикам в /public →
 *     stale-while-revalidate.
 *   - всё остальное (включая /api/*, /trees/*, /persons/*, и т.д.) →
 *     bypass (network-only). Никогда не отдаём из кэша.
 */

const CACHE_NAME = "autotreegen-static-v1";
const PRECACHE_URLS = ["/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS).catch(() => {})),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Только static-paths (Next build assets, public/). Всё динамическое
  // (/api/*, server-rendered pages) пропускаем напрямую — provenance
  // важнее offline.
  const isStatic = url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/static/");
  if (!isStatic) return;

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      const networkPromise = fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => cached);
      return cached || networkPromise;
    }),
  );
});
