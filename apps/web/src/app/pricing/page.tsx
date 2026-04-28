"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { BillingApiError, startCheckout } from "@/lib/billing-api";

/**
 * Public pricing page (Phase 12.0).
 *
 * Pre-auth: страница доступна без login (cookie/JWT не требуется),
 * но ``Subscribe`` кнопка требует авторизации. До Phase 4.10 mock auth:
 * фронт сам кладёт ``autotreegen.mock_user_id`` в localStorage.
 *
 * Числовые лимиты ниже зеркалируют ``billing_service.services.entitlements.PlanLimits``
 * — синхронизация с backend ручная (одно место правды на двух сторонах).
 * Phase 12.x: SSR-фетч /billing/plans-summary, чтобы UI и backend никогда
 * не разъезжались.
 */
export default function PricingPage() {
  const [error, setError] = useState<string | null>(null);

  const checkoutMutation = useMutation({
    mutationFn: () => startCheckout("pro"),
    onSuccess: (data) => {
      // Stripe Checkout URL → редирект.
      window.location.assign(data.checkout_url);
    },
    onError: (err: unknown) => {
      if (err instanceof BillingApiError) {
        setError(err.message);
      } else {
        setError("Unknown error during checkout");
      }
    },
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="text-3xl font-semibold">Pricing</h1>
      <p className="mt-2 text-muted-foreground">
        Choose a plan. Cancel anytime, subscription pro-rates automatically.
      </p>

      <div className="mt-10 grid gap-6 md:grid-cols-2">
        <PlanCard
          title="Free"
          price="$0"
          tagline="For exploring AutoTreeGen on a single small tree."
          features={[
            "1 family tree",
            "Up to 100 persons",
            "GEDCOM import / export",
            "Basic visualization",
          ]}
          notIncluded={["DNA analysis", "FamilySearch import", "AI assistant"]}
          cta={{ label: "Current plan", disabled: true }}
        />

        <PlanCard
          title="Pro"
          price="$9 / mo"
          tagline="For serious genealogists with DNA + multi-source research."
          highlight
          features={[
            "Unlimited trees",
            "Unlimited persons per tree",
            "DNA upload + clustering",
            "FamilySearch import (5/day)",
            "Hypothesis explainer (AI)",
            "Priority support",
          ]}
          notIncluded={[]}
          cta={{
            label: checkoutMutation.isPending ? "Redirecting…" : "Subscribe",
            disabled: checkoutMutation.isPending,
            onClick: () => {
              setError(null);
              checkoutMutation.mutate();
            },
          }}
        />
      </div>

      {error && (
        <div className="mt-6 rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      <p className="mt-12 text-xs text-muted-foreground">
        Payments handled by Stripe. We do not store your card details. See our privacy policy for
        details on subscription data we keep.
      </p>
    </main>
  );
}

type PlanCardProps = {
  title: string;
  price: string;
  tagline: string;
  features: string[];
  notIncluded: string[];
  highlight?: boolean;
  cta: {
    label: string;
    disabled?: boolean;
    onClick?: () => void;
  };
};

function PlanCard({ title, price, tagline, features, notIncluded, highlight, cta }: PlanCardProps) {
  return (
    <div
      className={
        highlight
          ? "rounded-lg border-2 border-primary bg-card p-6 shadow"
          : "rounded-lg border bg-card p-6"
      }
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-xl font-semibold">{title}</h2>
        <span className="text-2xl font-bold">{price}</span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{tagline}</p>

      <ul className="mt-4 space-y-2 text-sm">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2">
            <span aria-hidden className="text-emerald-600">
              {"✓"}
            </span>
            <span>{f}</span>
          </li>
        ))}
        {notIncluded.map((f) => (
          <li key={f} className="flex items-start gap-2 text-muted-foreground line-through">
            <span aria-hidden>{"·"}</span>
            <span>{f}</span>
          </li>
        ))}
      </ul>

      <button
        type="button"
        disabled={cta.disabled}
        onClick={cta.onClick}
        className={
          highlight
            ? "mt-6 w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            : "mt-6 w-full rounded-md border px-4 py-2 text-sm font-medium disabled:opacity-50"
        }
      >
        {cta.label}
      </button>
    </div>
  );
}
