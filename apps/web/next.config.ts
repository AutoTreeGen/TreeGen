import type { NextConfig } from "next";

/**
 * Конфигурация Next.js для read-only tree-view.
 * Server-первичный рендер, без статичного export — нужен runtime для
 * client-side TanStack Query (Phase 4.2 добавит auth middleware).
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
