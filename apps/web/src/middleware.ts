/**
 * Next.js middleware: Clerk auth gate (Phase 4.10, ADR-0033).
 *
 * Защищает все страницы кроме whitelist'а:
 * - ``/`` — public landing.
 * - ``/sign-in`` / ``/sign-up`` — Clerk-hosted UI компонентов.
 * - ``/api/webhooks/*`` — внешние webhook'и (Clerk webhook receiver не
 *   тут, а на parser-service ``/webhooks/clerk``; здесь whitelist на
 *   будущее, чтобы любой fronted webhook proxy мимо middleware'а).
 *
 * `clerkMiddleware` сам не делает редиректа: мы явно зовём
 * `auth.protect()` для непубличных маршрутов. См. Clerk docs §
 * "Protect routes" (Next.js 15+, ``clerkMiddleware`` API).
 */

import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import type { NextRequest } from "next/server";

// Pages, которые остаются доступны без аутентификации.
const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/api/webhooks/(.*)",
]);

export default clerkMiddleware(async (auth, req: NextRequest) => {
  if (!isPublicRoute(req)) {
    // ``protect()`` возвращает 401/redirect для неаутентифицированных.
    // По дефолту Clerk редиректит на NEXT_PUBLIC_CLERK_SIGN_IN_URL
    // (или ``/sign-in``). Кастомный redirect — через
    // ``unauthenticatedUrl`` в clerkMiddleware-options, если понадобится.
    await auth.protect();
  }
});

export const config = {
  // Защищаем всё кроме статики Next.js и явных public assets.
  // Шаблон взят из официальных доков Clerk (App Router + Next 15).
  matcher: [
    // Skip Next.js internals и all static files (kept in public/).
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run для API routes.
    "/(api|trpc)(.*)",
  ],
};
