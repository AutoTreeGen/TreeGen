import { useTranslations } from "next-intl";
import Link from "next/link";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { NotificationBell } from "@/components/notification-bell";
import { cn } from "@/lib/utils";

/**
 * Phase 4.13 — расширили шапку: добавили LocaleSwitcher (раньше жил
 * только на лендинге, теперь доступен везде, чтобы юзер мог переключить
 * язык не выходя на /).
 *
 * Phase 4.2 добавит реальную навигацию по деревьям + профиль user'а —
 * расширим, не переписывая.
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
        <LocaleSwitcher />
        <NotificationBell />
      </div>
    </header>
  );
}
