"use client";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { CheckCircle2, Loader2, Mail } from "lucide-react";
import * as motion from "motion/react-client";
import { type FormEvent, useState } from "react";

type FormState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "success" }
  | { status: "error"; message: string };

/**
 * Waitlist форма с client-side validation. Отправка POST /api/waitlist —
 * Cloudflare Pages Function принимает и пишет в KV / шлёт notification.
 */
export function WaitlistForm() {
  const [state, setState] = useState<FormState>({ status: "idle" });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [wantsUpload, setWantsUpload] = useState(true);
  const [consent, setConsent] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!consent) {
      setState({
        status: "error",
        message: "Please confirm the privacy notice to continue.",
      });
      return;
    }

    setState({ status: "submitting" });

    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim(),
          name: name.trim() || null,
          wants_upload: wantsUpload,
          source: "landing-hero",
          submitted_at: new Date().toISOString(),
        }),
      });

      // Пытаемся прочитать JSON, чтобы получить структурированную ошибку
      // от нашего endpoint'а. Если это HTML (404 в dev / 500 от прокси) —
      // показываем безопасное generic сообщение, не дампим сырой ответ.
      const contentType = response.headers.get("Content-Type") ?? "";
      const isJson = contentType.includes("application/json");
      const payload: unknown = isJson ? await response.json().catch(() => null) : null;

      if (!response.ok) {
        const apiMessage =
          isJson &&
          payload &&
          typeof payload === "object" &&
          "error" in payload &&
          typeof (payload as { error: unknown }).error === "string"
            ? (payload as { error: string }).error
            : null;

        // В dev обычно нет endpoint'а — даём подсказку; в prod показываем
        // что вернул API или статус.
        const fallback =
          response.status === 404
            ? "Waitlist endpoint not deployed yet. The form will work after Cloudflare Pages deploy."
            : `Submission failed (${response.status}). Please try again.`;

        throw new Error(apiMessage ?? fallback);
      }

      setState({ status: "success" });
    } catch (err) {
      const message =
        err instanceof Error && err.message ? err.message : "Network error. Please try again.";
      setState({ status: "error", message });
    }
  }

  if (state.status === "success") {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="rounded-2xl bg-[var(--color-surface)] p-10 text-center
          shadow-[var(--shadow-card)] ring-1 ring-[var(--color-brand-200)]"
      >
        <div
          className="mx-auto mb-5 flex h-14 w-14 items-center justify-center
            rounded-full bg-[var(--color-brand-50)]"
        >
          <CheckCircle2 className="h-7 w-7 text-[var(--color-brand-600)]" strokeWidth={2.2} />
        </div>
        <h3 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
          You&apos;re on the list
        </h3>
        <p className="mx-auto mt-3 max-w-md text-pretty text-[var(--color-ink-600)]">
          We&apos;ll email you when early access opens. If you opted in to upload your GEDCOM,
          we&apos;ll send a secure link first.
        </p>
      </motion.div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl bg-[var(--color-surface)] p-8 shadow-[var(--shadow-card)]
        ring-1 ring-[var(--color-border)] sm:p-10"
    >
      <div className="grid gap-5 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="email"
            className="mb-2 block text-sm font-medium text-[var(--color-ink-700)]"
          >
            Email <span className="text-[var(--color-brand-600)]">*</span>
          </label>
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={state.status === "submitting"}
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="name"
            className="mb-2 block text-sm font-medium text-[var(--color-ink-700)]"
          >
            Name <span className="text-[var(--color-ink-400)]">(optional)</span>
          </label>
          <Input
            id="name"
            type="text"
            autoComplete="name"
            placeholder="How should we address you?"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={state.status === "submitting"}
          />
        </div>
      </div>

      <div className="mt-6 space-y-4">
        <label className="flex cursor-pointer items-start gap-3">
          <Checkbox
            checked={wantsUpload}
            onCheckedChange={(v) => setWantsUpload(v === true)}
            disabled={state.status === "submitting"}
            className="mt-0.5"
          />
          <span className="text-sm text-[var(--color-ink-700)] leading-relaxed">
            <strong className="font-medium text-[var(--color-ink-900)]">
              Yes — I want to upload my GEDCOM for analysis.
            </strong>{" "}
            We&apos;ll prioritise your invite when upload becomes available.
          </span>
        </label>

        <label className="flex cursor-pointer items-start gap-3">
          <Checkbox
            checked={consent}
            onCheckedChange={(v) => setConsent(v === true)}
            disabled={state.status === "submitting"}
            className="mt-0.5"
          />
          <span className="text-sm text-[var(--color-ink-600)] leading-relaxed">
            I&apos;ve read the{" "}
            <a
              href="/privacy/"
              className="font-medium text-[var(--color-brand-600)] underline-offset-2
                hover:underline"
            >
              privacy notice
            </a>{" "}
            and consent to receive email about early access. We never sell or share your data, and
            you can unsubscribe anytime.
          </span>
        </label>
      </div>

      {state.status === "error" && (
        <p
          role="alert"
          className="mt-5 rounded-xl bg-rose-50 px-4 py-3 text-sm
            text-rose-700 ring-1 ring-rose-200"
        >
          {state.message}
        </p>
      )}

      <div className="mt-7 flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-[var(--color-ink-500)]">
          <Mail className="mr-1 inline h-3.5 w-3.5" />
          We send fewer than 1 email per month. Promise.
        </p>
        <Button type="submit" size="lg" disabled={state.status === "submitting"}>
          {state.status === "submitting" ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Joining…
            </>
          ) : (
            <>Join the waitlist</>
          )}
        </Button>
      </div>
    </form>
  );
}
