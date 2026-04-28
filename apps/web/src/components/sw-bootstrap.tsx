"use client";

import { useEffect } from "react";

import { registerServiceWorker } from "@/lib/sw-registration";

/**
 * Client-only bootstrap для service worker'а.
 *
 * Отдельный компонент (а не inline-call в Providers), чтобы:
 * - SSR не вызывал registration logic;
 * - бы можно было unit-тестировать registration отдельно;
 * - визуально была видна точка инициализации в layout-tree.
 */
export function ServiceWorkerBootstrap() {
  useEffect(() => {
    void registerServiceWorker();
  }, []);
  return null;
}
