"use client";

/**
 * Phase 4.13 — единая i18n-aware error-карточка (ADR-0037).
 *
 * Идея: вместо ad-hoc строк типа `<p>Failed to load preferences.</p>` —
 * один компонент, который резолвит код ошибки в `errors.*` namespace
 * и опционально рисует кнопку retry. Локали приходят бесплатно.
 *
 * Поддерживаемые коды (см. messages/{en,ru}.json `errors`):
 *   - generic | network | unauthorized | forbidden | notFound | validation | rateLimit
 *   - доменные: preferencesLoadFailed | notificationsLoadFailed | treesLoadFailed | …
 *
 * Если передан незнакомый код — показывается `errors.generic` (next-intl
 * fallback) и в консоль уходит warning, чтобы dev заметил рассинхрон
 * между кодом и messages.
 */

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export type ErrorCode =
  | "generic"
  | "network"
  | "unauthorized"
  | "forbidden"
  | "notFound"
  | "validation"
  | "rateLimit"
  | "preferencesLoadFailed"
  | "notificationsLoadFailed"
  | "treesLoadFailed";

export type ErrorMessageProps = {
  /** Ключ из `errors.*` namespace. По умолчанию `generic`. */
  code?: ErrorCode;
  /** Если задан — рисуется кнопка retry с переданным callback'ом. */
  onRetry?: (() => void) | null;
  /** Опциональный override label'а у retry-кнопки (default — `common.tryAgain`). */
  retryLabel?: string;
  className?: string;
};

export function ErrorMessage({
  code = "generic",
  onRetry,
  retryLabel,
  className,
}: ErrorMessageProps) {
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const message = tErrors(code);

  return (
    <div
      role="alert"
      className={
        className ??
        "flex flex-col items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
      }
    >
      <p>{message}</p>
      {onRetry ? (
        <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
          {retryLabel ?? tCommon("tryAgain")}
        </Button>
      ) : null}
    </div>
  );
}
