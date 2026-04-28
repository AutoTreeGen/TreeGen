"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { BillingApiError, fetchCurrentPlan, openPortal } from "@/lib/billing-api";

/**
 * Settings → Billing (Phase 12.0).
 *
 * Показывает текущий план + meta + кнопку "Manage subscription"
 * (редирект в Stripe Customer Portal).
 *
 * При plan=FREE — CTA на /pricing.
 */
export default function BillingSettingsPage() {
  const [error, setError] = useState<string | null>(null);

  const planQuery = useQuery({
    queryKey: ["billing-current-plan"],
    queryFn: fetchCurrentPlan,
  });

  const portalMutation = useMutation({
    mutationFn: openPortal,
    onSuccess: (data) => {
      window.location.assign(data.portal_url);
    },
    onError: (err: unknown) => {
      if (err instanceof BillingApiError) {
        setError(err.message);
      } else {
        setError("Unknown error opening billing portal");
      }
    },
  });

  if (planQuery.isLoading) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (planQuery.isError || !planQuery.data) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm text-destructive">
          Failed to load billing info. Please try again later.
        </p>
      </main>
    );
  }

  const { plan, status, current_period_end, cancel_at_period_end, limits } = planQuery.data;
  const periodEnd = current_period_end ? new Date(current_period_end) : null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-semibold">Billing</h1>

      <section className="mt-8 rounded-lg border bg-card p-6">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xl font-semibold uppercase tracking-wide">{plan}</h2>
          {status && (
            <span className="text-sm text-muted-foreground">
              status: <span className="font-mono">{status}</span>
            </span>
          )}
        </div>

        {periodEnd && (
          <p className="mt-2 text-sm text-muted-foreground">
            {cancel_at_period_end ? "Subscription ends on " : "Renews on "}
            <span className="font-medium">{periodEnd.toLocaleDateString()}</span>
          </p>
        )}

        <ul className="mt-4 space-y-1 text-sm">
          <li>
            Trees: <span className="font-mono">{limits.max_trees ?? "unlimited"}</span>
          </li>
          <li>
            Persons / tree:{" "}
            <span className="font-mono">{limits.max_persons_per_tree ?? "unlimited"}</span>
          </li>
          <li>DNA upload: {limits.dna_enabled ? "enabled" : "disabled"}</li>
          <li>FamilySearch import: {limits.fs_import_enabled ? "enabled" : "disabled"}</li>
        </ul>

        <div className="mt-6 flex gap-3">
          {plan === "pro" ? (
            <button
              type="button"
              disabled={portalMutation.isPending}
              onClick={() => {
                setError(null);
                portalMutation.mutate();
              }}
              className="rounded-md border px-4 py-2 text-sm font-medium disabled:opacity-50"
            >
              {portalMutation.isPending ? "Redirecting…" : "Manage subscription"}
            </button>
          ) : (
            <a
              href="/pricing"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Upgrade to Pro
            </a>
          )}
        </div>

        {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
      </section>
    </main>
  );
}
