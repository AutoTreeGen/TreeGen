import { LetterIcon } from "@/components/icons/letter";
import { Logo } from "@/components/logo";

/** Footer с лого, контактами и privacy link. */
export function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-[var(--color-border)] bg-[var(--color-surface-muted)]">
      <div className="container mx-auto max-w-6xl px-6 py-12">
        <div className="flex flex-col items-start gap-8 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <Logo size="md" />
            <p className="mt-3 max-w-md text-sm text-[var(--color-ink-600)]">
              Evidence-based scientific genealogy. Built with care for the long-running research
              community.
            </p>
          </div>

          <nav className="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
            <a
              href="/privacy/"
              className="text-[var(--color-ink-700)] hover:text-[var(--color-brand-700)]"
            >
              Privacy
            </a>
            <a
              href="mailto:hello@autotreegen.com"
              className="inline-flex items-center gap-2 text-[var(--color-ink-700)]
                hover:text-[var(--color-brand-700)]"
            >
              <LetterIcon className="h-5 w-5" />
              hello@autotreegen.com
            </a>
          </nav>
        </div>

        <div
          className="mt-10 flex flex-col gap-3 border-t border-[var(--color-border)]
            pt-6 text-xs text-[var(--color-ink-500)] sm:flex-row sm:items-center
            sm:justify-between"
        >
          <p>© {year} AutoTreeGen. All rights reserved.</p>
          <p>Made with care for serious researchers.</p>
        </div>
      </div>
    </footer>
  );
}
