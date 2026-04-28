"use client";

import { useEffect, useState } from "react";

/**
 * Upgrade modal (Phase 12.0).
 *
 * Слушает custom event ``autotreegen:payment-required``, который выкидывают
 * api-обёртки при получении 402 от backend'а. Detail event'а соответствует
 * Pydantic ``PaymentRequiredDetail`` от billing-service.
 *
 * Использование:
 *
 * ```ts
 * window.dispatchEvent(
 *   new CustomEvent('autotreegen:payment-required', { detail: paymentRequiredDetail })
 * );
 * ```
 *
 * Этот компонент включается в корневой layout, чтобы один inst мог
 * перехватывать события от любого endpoint'а в приложении.
 */
type PaymentRequiredDetail = {
  feature: string;
  current_plan: string;
  upgrade_url: string;
  message: string;
};

export function UpgradeModal() {
  const [detail, setDetail] = useState<PaymentRequiredDetail | null>(null);

  useEffect(() => {
    function onEvent(event: Event) {
      const ce = event as CustomEvent<PaymentRequiredDetail>;
      if (ce.detail) {
        setDetail(ce.detail);
      }
    }
    window.addEventListener("autotreegen:payment-required", onEvent);
    return () => window.removeEventListener("autotreegen:payment-required", onEvent);
  }, []);

  if (!detail) return null;

  return (
    <dialog
      open
      aria-labelledby="upgrade-modal-title"
      className="fixed inset-0 z-50 m-0 flex h-full w-full items-center justify-center bg-black/50 p-4"
      onClose={() => setDetail(null)}
      onClick={() => setDetail(null)}
      onKeyDown={(e) => {
        if (e.key === "Escape") setDetail(null);
      }}
    >
      {/* Inner panel — клики и keyboard-events не закрывают modal. */}
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: stopPropagation only,
          all interactions live on dedicated buttons below. */}
      <div
        className="max-w-md rounded-lg border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="upgrade-modal-title" className="text-xl font-semibold">
          Upgrade required
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">{detail.message}</p>
        <p className="mt-4 text-xs text-muted-foreground">
          Current plan: <span className="font-mono">{detail.current_plan}</span>
        </p>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={() => setDetail(null)}
            className="rounded-md border px-4 py-2 text-sm font-medium"
          >
            Maybe later
          </button>
          <a
            href={detail.upgrade_url}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            See plans
          </a>
        </div>
      </div>
    </dialog>
  );
}
