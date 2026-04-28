"use client";

/**
 * Service worker registration helper (Phase 4.6, ADR-0041).
 *
 * Регистрируется только в production-сборке — в dev'е Next.js HMR
 * конфликтует со sw cache'ем (видишь stale страницы при изменениях
 * в файлах). Также skip'ается в test-environment'е (jsdom не имеет
 * service-worker API).
 */

export async function registerServiceWorker(): Promise<void> {
  if (typeof window === "undefined") return; // SSR no-op
  if (!("serviceWorker" in navigator)) return;
  if (process.env.NODE_ENV !== "production") return;

  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (err) {
    // Регистрация sw — best-effort. Если не получилось (например,
    // расширение блокирует), приложение работает как обычный SPA.
    if (process.env.NODE_ENV !== "production") {
      // eslint-disable-next-line no-console
      console.warn("[sw] registration failed", err);
    }
  }
}
