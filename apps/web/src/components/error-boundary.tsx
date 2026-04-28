"use client";

/**
 * Error boundaries (Phase 4.6, ADR-0041).
 *
 * Two flavours:
 *
 * * ``GlobalErrorBoundary`` — обёртка вокруг всего root layout'а. При
 *   падении показывает full-page fallback с ссылкой на support@ и
 *   кнопкой «Try again» (resetErrorBoundary).
 * * ``SectionErrorBoundary`` — изолирует один раздел приложения
 *   (/trees, /dna, /sources, /hypotheses, /persons). При падении
 *   рендерит inline-fallback внутри секции, остальное приложение
 *   продолжает работать.
 *
 * Обе обёртки построены на ``react-error-boundary@6``; мы держим
 * fallback-UI рядом с ``ErrorBoundary``, чтобы i18n keys и стили жили
 * в одном файле.
 */

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { type FallbackProps, ErrorBoundary as RebErrorBoundary } from "react-error-boundary";

const SUPPORT_EMAIL = "support@autotreegen.com";

/**
 * Narrow react-error-boundary's ``error: unknown`` в плотный shape с
 * name/message. Любые non-Error throws ("strings as exceptions") тоже
 * переживают — отдадим имя ``Error`` и string-coerced message.
 */
function asError(error: unknown): { name: string; message: string } {
  if (error instanceof Error) return { name: error.name, message: error.message };
  return { name: "Error", message: String(error) };
}

/**
 * Build a mailto: link с pre-filled subject + body containing
 * error.name + error.message + URL — облегчает triage в support inbox'е.
 */
function buildReportMailto(error: unknown): string {
  const e = asError(error);
  const subject = encodeURIComponent(`[autotreegen] error: ${e.name}`);
  const url = typeof window !== "undefined" ? window.location.href : "";
  const body = encodeURIComponent(
    [
      "Hi,",
      "",
      "I hit an error in AutoTreeGen.",
      "",
      `Error: ${e.name}: ${e.message}`,
      `Page: ${url}`,
      "",
      "(Please leave the lines above so we can match it to our logs.)",
      "",
    ].join("\n"),
  );
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`;
}

/**
 * Full-page fallback. Использует только raw HTML/Tailwind utilities,
 * чтобы не зависеть от компонентов, которые сами могли упасть.
 */
function GlobalFallback({ error, resetErrorBoundary }: FallbackProps) {
  const t = useTranslations("errors");
  const e = asError(error);
  return (
    <div
      role="alert"
      className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-start justify-center gap-4 px-6 py-12"
    >
      <h1 className="text-2xl font-semibold text-[var(--color-ink-900)]">{t("globalTitle")}</h1>
      <p className="text-base text-[var(--color-ink-700)]">{t("globalBody")}</p>
      <pre className="max-h-40 w-full overflow-auto rounded-md border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3 text-xs text-[var(--color-ink-600)]">
        {e.name}: {e.message}
      </pre>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={resetErrorBoundary}
          className="rounded-md bg-[var(--color-brand-600)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--color-brand-700)]"
        >
          {t("tryAgain")}
        </button>
        <a
          href={buildReportMailto(error)}
          className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm font-medium text-[var(--color-ink-800)] hover:bg-[var(--color-surface-muted)]"
        >
          {t("reportIssue")}
        </a>
        <a
          href="/"
          className="rounded-md px-4 py-2 text-sm font-medium text-[var(--color-ink-700)] hover:underline"
        >
          {t("goHome")}
        </a>
      </div>
    </div>
  );
}

/**
 * Inline-fallback под секцию. Рендерится поверх content-area,
 * шапка/sidebar остаются работать.
 */
function SectionFallback({ error, resetErrorBoundary }: FallbackProps) {
  const t = useTranslations("errors");
  const e = asError(error);
  return (
    <div
      role="alert"
      className="mx-auto my-6 flex max-w-2xl flex-col gap-3 rounded-lg border border-[var(--color-destructive-border,#f5c2c7)] bg-[var(--color-destructive-bg,#fdf2f2)] p-5"
    >
      <h2 className="text-lg font-semibold text-[var(--color-ink-900)]">{t("sectionTitle")}</h2>
      <p className="text-sm text-[var(--color-ink-700)]">{t("sectionBody")}</p>
      <pre className="max-h-32 w-full overflow-auto rounded border border-[var(--color-border)] bg-white p-2 text-xs text-[var(--color-ink-600)]">
        {e.name}: {e.message}
      </pre>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={resetErrorBoundary}
          className="rounded-md bg-[var(--color-brand-600)] px-3 py-1.5 text-sm font-medium text-white hover:bg-[var(--color-brand-700)]"
        >
          {t("tryAgain")}
        </button>
        <a
          href={buildReportMailto(error)}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-ink-800)] hover:bg-white"
        >
          {t("reportIssue")}
        </a>
      </div>
    </div>
  );
}

type Props = { children: ReactNode };

/**
 * Console-log нerror'ов в dev; в prod — потенциальный sink в Sentry
 * (Phase 13.x). Проверка через ``process.env.NODE_ENV`` гарантирует, что
 * production не плюётся stack-trace'ами в DevTools конечного пользователя.
 *
 * react-error-boundary@6 типизирует error как ``unknown`` (раньше Error);
 * ловим как unknown и не делаем assumptions о shape.
 */
function logBoundaryError(error: unknown, info: { componentStack?: string | null }): void {
  if (process.env.NODE_ENV !== "production") {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info.componentStack);
  }
  // TODO Phase 13.x: Sentry.captureException(error, { contexts: { react: info } })
}

export function GlobalErrorBoundary({ children }: Props) {
  return (
    <RebErrorBoundary FallbackComponent={GlobalFallback} onError={logBoundaryError}>
      {children}
    </RebErrorBoundary>
  );
}

export function SectionErrorBoundary({ children }: Props) {
  return (
    <RebErrorBoundary FallbackComponent={SectionFallback} onError={logBoundaryError}>
      {children}
    </RebErrorBoundary>
  );
}
