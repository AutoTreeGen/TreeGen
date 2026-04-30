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
 *
 * Phase 13.2 (ADR-0053): security headers + CSP. CSP пока без nonce
 * (см. ADR-0053 §«CSP nonce roadmap»); это compromise чтобы не
 * блокировать prod-launch на нюансах middleware. Строгий вариант с
 * 'strict-dynamic' + nonce — план Phase 13.3.
 */

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

// CSP-источник правды. Менять synchronized с docs/adr/0053-production-security-hardening.md.
const CSP_DIRECTIVES: Record<string, string[]> = {
  "default-src": ["'self'"],
  // 'unsafe-inline' — нужно для Next.js inline scripts (hydration).
  // 'unsafe-eval' — Next.js dev-режиму. В prod-build не используется
  // приложением, но Cloud Run-инстанс гоняет тот же бандл что и dev,
  // поэтому keep'аем минимально-совместимым (см. ADR-0053).
  "script-src": [
    "'self'",
    "'unsafe-inline'",
    "'unsafe-eval'",
    "https://*.clerk.com",
    "https://*.clerk.accounts.dev",
    "https://challenges.cloudflare.com", // Clerk использует Cloudflare Turnstile для bot-protection.
  ],
  "style-src": ["'self'", "'unsafe-inline'"], // Tailwind + Next.js inject inline styles.
  "img-src": ["'self'", "blob:", "data:", "https:"], // Clerk avatars приходят с разных CDN.
  "font-src": ["'self'", "data:"],
  "connect-src": [
    "'self'",
    "https://*.clerk.com",
    "https://*.clerk.accounts.dev",
    "wss://*.clerk.com",
    // API gateway URL — задаётся через env в build-time. Если не задан, строгий
    // 'self' заблокирует cross-origin XHR; pre-prod указывает явный allow-list.
    process.env.NEXT_PUBLIC_API_URL ?? "",
  ].filter(Boolean),
  "frame-src": ["'self'", "https://*.clerk.com", "https://challenges.cloudflare.com"],
  "frame-ancestors": ["'none'"], // X-Frame-Options=DENY equivalent.
  "form-action": ["'self'"],
  "base-uri": ["'self'"],
  "object-src": ["'none'"],
  "upgrade-insecure-requests": [],
};

function buildCsp(directives: Record<string, string[]>): string {
  return Object.entries(directives)
    .map(([directive, values]) =>
      values.length > 0 ? `${directive} ${values.join(" ")}` : directive,
    )
    .join("; ");
}

const SECURITY_HEADERS = [
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains; preload",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=()",
  },
  { key: "Content-Security-Policy", value: buildCsp(CSP_DIRECTIVES) },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",

  // Phase 13.2 — image domains whitelist для next/image. Cloud Run worker
  // тут отдаёт SSR; неwhitelisted домены вернут 400.
  images: {
    remotePatterns: [
      // Clerk-хостит avatars на нескольких субдоменах (img.clerk.com и др.).
      { protocol: "https", hostname: "*.clerk.com" },
      { protocol: "https", hostname: "img.clerk.com" },
      // Gravatar fallback — Clerk fallback'ает на него для users без custom avatar.
      { protocol: "https", hostname: "www.gravatar.com" },
    ],
  },

  // Phase 13.2 — security headers ко всем routes.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
