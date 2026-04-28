"use client";

/**
 * Offline indicator (Phase 4.6, ADR-0041).
 *
 * Слушает ``window.online``/``offline`` events + initial ``navigator.onLine``.
 * При offline показывает sticky-banner вверху страницы. При возвращении в
 * online — invalidates всех react-query queries, чтобы UI перетянул свежие
 * данные.
 *
 * Реализация:
 *
 * * SSR-safe: на сервере ``navigator`` не существует → начальное
 *   состояние всегда "online" (no banner). Real-state считывается на
 *   client-mount.
 * * QueryClient invalidation работает через ``useQueryClient`` —
 *   компонент должен жить ВНУТРИ ``<QueryClientProvider>`` (root layout
 *   гарантирует через ``Providers``).
 */

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

export function OfflineIndicator() {
  // SSR-safe initial: "online". Реальное состояние подтягиваем на mount.
  const [online, setOnline] = useState(true);
  const queryClient = useQueryClient();
  const t = useTranslations("offline");

  useEffect(() => {
    // Sync с реальным navigator.onLine на mount.
    setOnline(navigator.onLine);

    function handleOnline() {
      setOnline(true);
      // Re-fetch всех stale queries — пользователь видит свежие данные
      // как только сеть вернулась.
      void queryClient.invalidateQueries();
    }
    function handleOffline() {
      setOnline(false);
    }

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [queryClient]);

  if (online) return null;

  return (
    <output
      aria-live="polite"
      data-testid="offline-banner"
      className="sticky top-0 z-50 block w-full bg-[var(--color-warning-bg,#fff7ed)] px-4 py-2 text-center text-sm text-[var(--color-warning-fg,#9a3412)] shadow-sm"
    >
      {t("banner")}
    </output>
  );
}
