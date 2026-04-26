import { Footer } from "@/components/footer";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Notice",
  description: "How AutoTreeGen handles your data — what we collect, why, and how to delete it.",
  robots: { index: true, follow: true },
};

/**
 * Privacy notice — закрывает базовые требования AU Privacy Act + GDPR
 * для landing-фазы (только waitlist email). Когда появится upload —
 * этот документ должен быть существенно расширен и ревьювнут юристом.
 */
export default function PrivacyPage() {
  return (
    <>
      <main className="mx-auto max-w-3xl px-6 py-20 sm:py-28">
        <div className="mb-10">
          <a
            href="/"
            className="inline-flex items-center gap-1 text-sm font-medium
              text-[var(--color-brand-600)] hover:underline"
          >
            ← Back to home
          </a>
        </div>

        <h1
          className="font-display text-4xl font-bold tracking-tight
            text-[var(--color-ink-900)] sm:text-5xl"
        >
          Privacy notice
        </h1>
        <p className="mt-3 text-sm text-[var(--color-ink-500)]">Last updated: April 2026</p>

        <div className="prose-content mt-10 space-y-6 text-[var(--color-ink-700)] leading-relaxed">
          <p>
            This page explains how AutoTreeGen (&ldquo;we&rdquo;, &ldquo;us&rdquo;) handles personal
            information you share at this stage of the project. The product is in private alpha —
            only the waitlist form on this site collects data right now. When upload functionality
            opens, this notice will be expanded.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            What we collect
          </h2>
          <ul className="list-disc space-y-2 pl-6">
            <li>
              <strong>Email address</strong> (required) — to notify you when early access opens.
            </li>
            <li>
              <strong>Name</strong> (optional) — to address you personally in email.
            </li>
            <li>
              <strong>Upload interest flag</strong> — whether you want to prioritise upload access.
            </li>
            <li>
              <strong>Technical metadata</strong> — IP address (hashed for rate limiting), country
              (from Cloudflare), browser user agent, submission timestamp.
            </li>
          </ul>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            What we do with it
          </h2>
          <ul className="list-disc space-y-2 pl-6">
            <li>Send fewer than one email per month about early access milestones.</li>
            <li>Prevent spam abuse (rate limiting via hashed IP).</li>
            <li>Understand rough geography of interest — country only.</li>
          </ul>
          <p>
            <strong>We never:</strong> sell your data, share it with advertisers, use it for
            targeted advertising, or train AI models on it.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            Where it lives
          </h2>
          <p>
            Cloudflare Workers KV (encrypted at rest), with global edge replication managed by
            Cloudflare. We do not host a separate database for waitlist data. Records auto-expire
            after 365 days unless you actively engage with us.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            Your rights
          </h2>
          <p>
            Under the Australian Privacy Act 1988 and the EU GDPR, you have the right to access,
            correct, or delete your data. To exercise these rights, email{" "}
            <a
              href="mailto:hello@autotreegen.com"
              className="text-[var(--color-brand-600)] underline"
            >
              hello@autotreegen.com
            </a>{" "}
            from the address you signed up with — we&apos;ll respond within 30 days.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            DNA and GEDCOM data
          </h2>
          <p>
            <strong>We do not currently accept any genealogy or DNA data through this site.</strong>{" "}
            When upload becomes available, separate consent and a much more detailed privacy policy
            will apply, including encryption, obfuscation of living individuals, and explicit
            data-deletion workflows.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            Changes
          </h2>
          <p>
            If we materially change this notice, we&apos;ll email everyone on the waitlist before
            the new terms take effect.
          </p>

          <h2 className="font-display text-2xl font-semibold text-[var(--color-ink-900)]">
            Contact
          </h2>
          <p>
            <a
              href="mailto:hello@autotreegen.com"
              className="text-[var(--color-brand-600)] underline"
            >
              hello@autotreegen.com
            </a>
          </p>
        </div>
      </main>
      <Footer />
    </>
  );
}
