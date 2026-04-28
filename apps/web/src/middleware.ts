/**
 * Combined middleware: Clerk auth (Phase 4.10) + i18n locale detection (Phase 4.12).
 *
 * Pipeline на каждый запрос:
 *
 * 1. Локаль: читаем ``NEXT_LOCALE``-cookie, иначе detect по
 *    ``Accept-Language`` и фиксируем cookie на 1 год. Применяется
 *    только к marketing-роутам (см. ``LOCALE_MATCHER``).
 * 2. Auth: для непубличных pages вызываем ``auth.protect()``;
 *    Clerk сам редиректит на sign-in.
 *
 * Clerk middleware-callback async — locale-side-effect делается
 * **до** ``auth.protect()`` и работает на любом запросе (even
 * unauthenticated), потому что cookie set'тится в response headers,
 * не зависит от user-state.
 */

import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { type NextRequest, NextResponse } from "next/server";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  asSupportedLocale,
  detectLocaleFromAcceptLanguage,
} from "./i18n/config";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

// Pages, которые остаются доступны без аутентификации.
const isPublicRoute = createRouteMatcher([
  "/",
  "/demo",
  "/onboarding",
  "/pricing",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/api/webhooks/(.*)",
]);

// Pathname'ы, на которых имеет смысл фиксировать locale-cookie. Мы
// не трогаем authed-роуты — их UI Phase 4.13 переведёт явно.
const LOCALE_PATHS = new Set(["/", "/demo", "/onboarding", "/pricing"]);

function ensureLocaleCookie(req: NextRequest, response: NextResponse): NextResponse {
  if (!LOCALE_PATHS.has(req.nextUrl.pathname)) {
    return response;
  }
  const existing = req.cookies.get(LOCALE_COOKIE)?.value;
  if (existing && asSupportedLocale(existing) === existing) {
    return response;
  }
  const detected = detectLocaleFromAcceptLanguage(req.headers.get("accept-language"));
  response.cookies.set({
    name: LOCALE_COOKIE,
    value: detected ?? DEFAULT_LOCALE,
    path: "/",
    sameSite: "lax",
    maxAge: ONE_YEAR_SECONDS,
  });
  return response;
}

export default clerkMiddleware(async (auth, req: NextRequest) => {
  // Compose: locale → auth. Clerk callback может вернуть Response
  // напрямую (например, redirect для signed-out на protected); если
  // мы вернули кастомный response, locale-cookie пропадёт. Поэтому
  // locale-set делаем на NextResponse.next(), а auth.protect()
  // отрабатывает после — он либо throw'ит redirect (Clerk обработает
  // сам), либо ничего не делает.
  const response = NextResponse.next();
  ensureLocaleCookie(req, response);
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
  return response;
});

export const config = {
  // Защищаем всё кроме статики Next.js и явных public assets.
  // Шаблон взят из официальных доков Clerk (App Router + Next 15).
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
