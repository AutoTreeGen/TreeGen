/**
 * Phase 4.12 — конфигурация next-intl.
 *
 * Стратегия:
 *   - public marketing pages (`/`, `/demo`, `/onboarding`, `/pricing`)
 *     используют next-intl c locale-aware рендером;
 *   - локаль определяется middleware'ом из cookie `NEXT_LOCALE`,
 *     fallback — `Accept-Language` header, fallback — `defaultLocale`;
 *   - URL'ы НЕ префиксуются локалью (`/`, не `/en` / `/ru`) — это
 *     foundation Phase 4.12. Phase 4.13 промоутирует на pathname-prefix
 *     (см. ADR-0035 §«Когда пересмотреть»).
 *
 * Authenticated pages (persons, dna, ...) пока БЕЗ i18n — оставлены
 * на английском до Phase 4.13.
 */

export const SUPPORTED_LOCALES = ["en", "ru"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_COOKIE = "NEXT_LOCALE";

/** Вернуть locale, если она поддерживаемая; иначе default. */
export function asSupportedLocale(value: string | null | undefined): Locale {
  if (!value) return DEFAULT_LOCALE;
  const normalised = value.toLowerCase().split("-")[0];
  return (SUPPORTED_LOCALES as readonly string[]).includes(normalised ?? "")
    ? (normalised as Locale)
    : DEFAULT_LOCALE;
}

/**
 * Определить locale из ``Accept-Language`` header.
 *
 * Простая реализация: первый supported язык в q-ordered list. Без
 * учёта q-весов (Phase 4.12 foundation; для production-grade negotiation
 * — следующая фаза, см. ADR-0035).
 */
export function detectLocaleFromAcceptLanguage(header: string | null): Locale {
  if (!header) return DEFAULT_LOCALE;
  for (const part of header.split(",")) {
    const tag = part.split(";")[0]?.trim();
    if (!tag) continue;
    // Берём только базовый язык (en-US → en) и сравниваем с supported.
    const base = tag.toLowerCase().split("-")[0];
    if (base && (SUPPORTED_LOCALES as readonly string[]).includes(base)) {
      return base as Locale;
    }
  }
  return DEFAULT_LOCALE;
}
