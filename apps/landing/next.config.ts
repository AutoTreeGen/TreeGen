import type { NextConfig } from "next";

/**
 * Конфигурация Next.js для landing-страницы.
 * Static export — деплой на Cloudflare Pages как статика.
 * API endpoints — через Cloudflare Pages Functions в `functions/`.
 */
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
