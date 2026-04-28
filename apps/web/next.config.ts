import type { NextConfig } from "next";

/**
 * Конфигурация Next.js для read-only tree-view.
 * Server-первичный рендер, без статичного export — нужен runtime для
 * client-side TanStack Query (Phase 4.2 добавит auth middleware).
 *
 * Phase 13.0: ``output: "standalone"`` собирает minimal Node.js bundle с
 * только нужными node_modules — это образ Cloud Run Dockerfile поверх
 * `node:slim` уменьшает с ~1 ГБ до ~150 МБ.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
};

export default nextConfig;
