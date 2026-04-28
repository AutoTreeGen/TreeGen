import type { MetadataRoute } from "next";

/**
 * Phase 4.12 — sitemap.xml для marketing-страниц.
 *
 * Генерируется динамически Next.js на /sitemap.xml. Включаем только
 * public маркетинговые роуты — auth-protected /persons, /trees, /dna
 * не должны индексироваться (они за логином и приватные).
 */

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://autotreegen.com";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  const routes = ["", "demo", "pricing", "onboarding"];
  return routes.map((path) => ({
    url: `${SITE_URL}/${path}`.replace(/\/$/, ""),
    lastModified,
    changeFrequency: path === "" ? "weekly" : "monthly",
    priority: path === "" ? 1.0 : 0.7,
  }));
}
