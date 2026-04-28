import type { MetadataRoute } from "next";

/**
 * Phase 4.12 — robots.txt.
 *
 * Disallow на auth-protected секции — даже если Clerk middleware
 * редиректит на /sign-in, мы не хотим, чтобы поисковики ходили туда
 * и индексировали редиректы. Auth ещё не подключён, но правила
 * фиксируем здесь, чтобы было готово к Phase 4.10.
 */

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://autotreegen.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/demo", "/pricing", "/onboarding"],
        disallow: [
          "/api/",
          "/dashboard",
          "/persons/",
          "/trees/",
          "/sources/",
          "/hypotheses/",
          "/dna/",
          "/familysearch/",
          "/settings/",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
