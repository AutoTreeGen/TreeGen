import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

/**
 * Конфигурация Next.js для read-only tree-view.
 * Server-первичный рендер, без статичного export — нужен runtime для
 * client-side TanStack Query (Phase 4.2 добавит auth middleware).
 *
 * Phase 13.0: ``output: "standalone"`` собирает minimal Node.js bundle с
 * только нужными node_modules — это образ Cloud Run Dockerfile поверх
 * `node:slim` уменьшает с ~1 ГБ до ~150 МБ.
 *
 * Phase 4.12: next-intl plugin подключает `src/i18n/request.ts` как
 * runtime-loader messages для server components (см. ADR-0035).
 */

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
};

export default withNextIntl(nextConfig);
