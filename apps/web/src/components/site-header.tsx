import Link from "next/link";

import { SignInButton, SignedIn, SignedOut, UserButton } from "@clerk/nextjs";

import { NotificationBell } from "@/components/notification-bell";
import { cn } from "@/lib/utils";

/**
 * Минимальная верхняя плашка приложения (Phase 8.0 → 4.10).
 *
 * Phase 4.10: добавлен ``<UserButton>`` для signed-in юзеров и
 * ``<SignInButton>`` (показывается, если user'а нет) — Clerk делает
 * рендер в host-окружении. NotificationBell виден только signed-in.
 */
export function SiteHeader() {
  return (
    <header
      className={cn(
        "sticky top-0 z-40 flex h-12 items-center justify-between gap-4 border-b",
        "border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-4",
      )}
    >
      <Link href="/" className="text-sm font-semibold text-[color:var(--color-ink-900)]">
        AutoTreeGen
      </Link>
      <div className="flex items-center gap-3">
        <SignedIn>
          <NotificationBell />
          <UserButton afterSignOutUrl="/" />
        </SignedIn>
        <SignedOut>
          <SignInButton mode="modal">
            <button
              type="button"
              className="text-sm font-medium text-[color:var(--color-ink-900)] hover:underline"
            >
              Sign in
            </button>
          </SignInButton>
        </SignedOut>
      </div>
    </header>
  );
}
