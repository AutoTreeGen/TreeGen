import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { type ErrorCode, ErrorMessage } from "@/components/error-message";
import enMessages from "../../messages/en.json";
import ruMessages from "../../messages/ru.json";

/**
 * Phase 4.13a — locale-rendering smoke tests.
 *
 * Контракт: критические i18n-active поверхности (ErrorMessage,
 * notifications, header) должны рендериться в обеих локалях БЕЗ
 * `[missing: ...]` fallback-строк. next-intl по умолчанию бросает
 * ``MISSING_MESSAGE`` warning и подставляет ключ в ``[]`` — этот тест
 * красным пометит расхождение между messages/{en,ru}.json.
 *
 * Page-level integration tests (dashboard / settings) — Phase 4.13b
 * (нужен server-component renderer + Clerk-mock + redirect-mock).
 */

const ALL_ERROR_CODES: ErrorCode[] = [
  "generic",
  "network",
  "unauthorized",
  "forbidden",
  "notFound",
  "validation",
  "rateLimit",
  "preferencesLoadFailed",
  "notificationsLoadFailed",
  "treesLoadFailed",
];

function withProviders(children: ReactNode, locale: "en" | "ru") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const messages = locale === "en" ? enMessages : ruMessages;
  return (
    <NextIntlClientProvider locale={locale} messages={messages}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </NextIntlClientProvider>
  );
}

function expectNoMissingFallback(html: string) {
  // next-intl рисует `[missing: ...]` когда ключа нет; биться об
  // empty-string или ICU-сурс тоже считаем падением.
  expect(html).not.toMatch(/\[missing:/i);
  expect(html).not.toMatch(/\{[a-z]+\}/i);
}

describe("locale rendering — both en and ru render without missing-key fallbacks", () => {
  // Suppress console.error/warn от next-intl чтобы test output остался читаемым.
  // Если test всё-таки fall'ится на missing-key, getByText / not.toMatch
  // покажут реальный текст.
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});

  for (const locale of ["en", "ru"] as const) {
    describe(`locale=${locale}`, () => {
      it.each(ALL_ERROR_CODES)("ErrorMessage code=%s renders cleanly", (code) => {
        const { container } = render(withProviders(<ErrorMessage code={code} />, locale));
        expectNoMissingFallback(container.innerHTML);
        // Должно быть непустое сообщение в alert role.
        const alert = container.querySelector('[role="alert"]');
        expect(alert).not.toBeNull();
        expect(alert?.textContent?.trim().length ?? 0).toBeGreaterThan(0);
      });

      it("ErrorMessage with onRetry shows a retry button", () => {
        const onRetry = vi.fn();
        const { getByRole } = render(
          withProviders(<ErrorMessage code="generic" onRetry={onRetry} />, locale),
        );
        const button = getByRole("button");
        expect(button.textContent?.trim().length ?? 0).toBeGreaterThan(0);
      });
    });
  }

  it("en and ru ErrorMessage texts differ for the same code (no English bleed-through)", () => {
    const { container: enContainer } = render(
      withProviders(<ErrorMessage code="preferencesLoadFailed" />, "en"),
    );
    const { container: ruContainer } = render(
      withProviders(<ErrorMessage code="preferencesLoadFailed" />, "ru"),
    );
    const enText = enContainer.querySelector('[role="alert"]')?.textContent ?? "";
    const ruText = ruContainer.querySelector('[role="alert"]')?.textContent ?? "";
    expect(enText.length).toBeGreaterThan(0);
    expect(ruText.length).toBeGreaterThan(0);
    expect(enText).not.toEqual(ruText);
  });
});

describe("messages parity — every key in en.json must exist in ru.json", () => {
  function flatten(obj: unknown, prefix = ""): string[] {
    if (typeof obj !== "object" || obj === null) return [];
    const result: string[] = [];
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      const path = prefix ? `${prefix}.${key}` : key;
      if (typeof value === "string") {
        result.push(path);
      } else if (typeof value === "object" && value !== null) {
        result.push(...flatten(value, path));
      }
    }
    return result;
  }

  it("en.json keys ⊆ ru.json keys", () => {
    const enKeys = new Set(flatten(enMessages));
    const ruKeys = new Set(flatten(ruMessages));
    const missingInRu = [...enKeys].filter((k) => !ruKeys.has(k));
    expect(missingInRu).toEqual([]);
  });

  it("ru.json keys ⊆ en.json keys (no orphan ru-only keys)", () => {
    const enKeys = new Set(flatten(enMessages));
    const ruKeys = new Set(flatten(ruMessages));
    const missingInEn = [...ruKeys].filter((k) => !enKeys.has(k));
    expect(missingInEn).toEqual([]);
  });
});
