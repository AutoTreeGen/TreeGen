import Link from "next/link";

import { NotificationBell } from "@/components/notification-bell";
import { cn } from "@/lib/utils";

/**
 * Минимальная верхняя плашка приложения (Phase 8.0).
 *
 * Сейчас держит только notification bell + ссылку на главную. Phase 4.2
 * добавит навигацию по деревьям, профиль user'а и т.д. — расширим, не
 * переписывая.
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
      <NotificationBell />
    </header>
  );
}
