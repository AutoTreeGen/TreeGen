"use client";

/**
 * Phase 4.12 — waitlist form для landing page.
 *
 * POST /api/waitlist proxy'ит запрос в parser-service на ``/waitlist``
 * (см. apps/web/src/app/api/waitlist/route.ts). На клиенте — простая
 * валидация email через regex; backend делает строгую проверку через
 * Pydantic ``EmailStr``.
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type FormState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success" }
  | { kind: "error"; message: string };

export function WaitlistForm() {
  const t = useTranslations("landing.waitlist");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<FormState>({ kind: "idle" });

  const onSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!EMAIL_REGEX.test(email)) {
      setState({ kind: "error", message: t("errorInvalidEmail") });
      return;
    }
    setState({ kind: "submitting" });
    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, locale: navigator.language }),
      });
      if (!response.ok) {
        setState({ kind: "error", message: t("errorGeneric") });
        return;
      }
      setState({ kind: "success" });
      setEmail("");
    } catch {
      setState({ kind: "error", message: t("errorGeneric") });
    }
  };

  if (state.kind === "success") {
    // <output> — нативный элемент с implicit role="status" (a11y-семантика
    // «результат вычисления / live-update» — точно про waitlist-ack).
    return (
      <output className="block rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
        {t("success")}
      </output>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-stretch justify-center gap-2">
      <label htmlFor="waitlist-email" className="sr-only">
        {t("emailLabel")}
      </label>
      <Input
        id="waitlist-email"
        type="email"
        required
        autoComplete="email"
        placeholder={t("emailPlaceholder")}
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        disabled={state.kind === "submitting"}
        className="w-72"
      />
      <Button type="submit" variant="primary" size="md" disabled={state.kind === "submitting"}>
        {t("submit")}
      </Button>
      {state.kind === "error" ? (
        <p role="alert" className="basis-full text-sm text-red-700">
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
