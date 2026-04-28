"use client";

/**
 * Phase 4.12 — переключатель locale (en ↔ ru).
 *
 * Просто перезаписывает cookie ``NEXT_LOCALE`` и перезагружает
 * страницу — server-component locale читает её через
 * `next-intl/server`. Без URL-prefix (Phase 4.12 foundation).
 */

import { useLocale, useTranslations } from "next-intl";

import { LOCALE_COOKIE, type Locale, SUPPORTED_LOCALES } from "@/i18n/config";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

export function LocaleSwitcher() {
  const locale = useLocale() as Locale;
  const t = useTranslations("common");

  const onChange = (next: Locale) => {
    if (next === locale) return;
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
    window.location.reload();
  };

  return (
    <label className="flex items-center gap-2 text-xs text-[color:var(--color-ink-500)]">
      <span className="sr-only">{t("language")}</span>
      <select
        aria-label={t("language")}
        value={locale}
        onChange={(e) => onChange(e.target.value as Locale)}
        className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-2 py-1 text-xs"
      >
        {SUPPORTED_LOCALES.map((code) => (
          <option key={code} value={code}>
            {code === "en" ? t("english") : t("russian")}
          </option>
        ))}
      </select>
    </label>
  );
}
