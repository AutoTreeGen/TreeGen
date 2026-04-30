import { SignInButton, SignedIn, SignedOut, UserButton } from "@clerk/nextjs";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { NotificationBell } from "@/components/notification-bell";
import { TreePicker } from "@/components/tree-picker";
import { cn } from "@/lib/utils";

/**
 * Минимальная верхняя плашка приложения.
 *
 * Phase 4.10 — Clerk auth: ``<UserButton>`` для signed-in, ``<SignInButton>``
 * для signed-out. NotificationBell виден только signed-in.
 * Phase 4.13 — добавлен LocaleSwitcher (виден всегда, чтобы можно было
 * переключить язык до логина).
 */
export function SiteHeader() {
  const t = useTranslations("header");
  return (
    <header
      className={cn(
        "sticky top-0 z-40 flex h-12 items-center justify-between gap-4 border-b",
        "border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-4",
      )}
    >
      <Link
        href="/"
        aria-label={t("home")}
        className="text-sm font-semibold text-[color:var(--color-ink-900)]"
      >
        {t("appName")}
      </Link>
      <div className="flex items-center gap-3">
        <SignedIn>
          {/* Phase 11.1: tree-picker виден только signed-in user'ам и
              скрывается сам если у них 0 деревьев. */}
          <TreePicker />
        </SignedIn>
        <LocaleSwitcher />
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
