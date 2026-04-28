/**
 * Phase 4.12 — middleware locale detection.
 *
 * При первом визите на корневой / marketing-роут:
 *   - читаем cookie ``NEXT_LOCALE``;
 *   - если cookie нет — определяем locale по ``Accept-Language``;
 *   - выставляем cookie на ответ (max-age 1 год), чтобы дальнейшие
 *     запросы шли быстрее без re-detection.
 *
 * URL не переписывается (Phase 4.12 — без pathname-prefix для locale).
 * Auth-protected paths (persons/, dna/, trees/, etc.) не трогаем —
 * Phase 4.10 Clerk middleware подключится отдельно.
 */

import { type NextRequest, NextResponse } from "next/server";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  asSupportedLocale,
  detectLocaleFromAcceptLanguage,
} from "./i18n/config";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

export function middleware(req: NextRequest): NextResponse {
  const response = NextResponse.next();

  const existing = req.cookies.get(LOCALE_COOKIE)?.value;
  if (existing && asSupportedLocale(existing) === existing) {
    return response;
  }

  // Cookie ещё не выставлена — детектим из заголовка и фиксируем.
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

export const config = {
  // Применяем только к публичным marketing-роутам. Аутентифицированные
  // pages оставляем на английском до Phase 4.13.
  matcher: ["/", "/demo", "/onboarding", "/pricing"],
};
