"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type NotificationSummary,
  fetchNotifications,
  markNotificationRead,
  notificationDeepLink,
  notificationTitle,
} from "@/lib/notifications-api";
import { cn } from "@/lib/utils";

/**
 * Notification bell — иконка-колокольчик в шапке + dropdown.
 *
 * Поведение:
 *
 * 1. На монтирование забирает первые 10 unread (poll каждые 30 сек,
 *    react-query staleTime).
 * 2. Бейдж с числом unread.
 * 3. Click → toggle dropdown.
 * 4. Click элемента → mark-read mutation + переход по deep-link
 *    (если есть).
 * 5. Outside-click закрывает dropdown.
 *
 * Auth: пока mock через DEFAULT_USER_ID (см. notifications-api.ts).
 * Phase 4.x пробросит реальный user_id из session/JWT.
 */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => fetchNotifications({ unread: true, limit: 10 }),
    // 30 секунд — компромисс между свежестью и нагрузкой. Phase 8.3
    // (WebSocket) сделает push, polling уйдёт.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  const markRead = useMutation({
    mutationFn: (notificationId: string) => markNotificationRead(notificationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  // Outside-click закрывает dropdown. Используем mousedown + capture,
  // чтобы Link-навигация в dropdown'е успела отработать ДО закрытия.
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  const unreadCount = data?.unread ?? 0;
  const items = data?.items ?? [];

  const handleItemClick = (notification: NotificationSummary) => {
    markRead.mutate(notification.id);
    const link = notificationDeepLink(notification);
    if (link) {
      // Полная навигация — App Router сам обработает.
      // Не используем next/link здесь, т.к. dropdown управляется state'ом
      // и Link-prefetch не нужен для редкого click-flow.
      window.location.href = link;
    }
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <Button
        variant="ghost"
        size="sm"
        aria-label="Notifications"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
        data-testid="notification-bell-button"
        className="relative"
      >
        <BellIcon />
        {unreadCount > 0 ? (
          <span
            data-testid="notification-bell-badge"
            className={cn(
              "absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center",
              "rounded-full bg-[color:var(--color-accent)] px-1 text-xs font-semibold",
              "text-white",
            )}
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        ) : null}
      </Button>

      {open ? (
        <div
          role="menu"
          data-testid="notification-bell-dropdown"
          className={cn(
            "absolute right-0 top-full z-50 mt-2 w-80 rounded-md",
            "border border-[color:var(--color-border)] bg-[color:var(--color-surface)]",
            "shadow-lg",
          )}
        >
          <div
            className={cn(
              "border-b border-[color:var(--color-border)] px-3 py-2 text-sm",
              "font-medium text-[color:var(--color-ink-900)]",
            )}
          >
            Notifications
          </div>

          {isLoading ? (
            <div className="px-3 py-4 text-sm text-[color:var(--color-ink-500)]">Loading…</div>
          ) : items.length === 0 ? (
            <div className="px-3 py-4 text-sm text-[color:var(--color-ink-500)]">
              You&rsquo;re all caught up.
            </div>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {items.map((notification) => {
                const link = notificationDeepLink(notification);
                return (
                  <li key={notification.id}>
                    <button
                      type="button"
                      data-testid="notification-bell-item"
                      onClick={() => handleItemClick(notification)}
                      className={cn(
                        "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left",
                        "text-sm hover:bg-[color:var(--color-surface-muted)]",
                        link ? "cursor-pointer" : "cursor-default",
                      )}
                    >
                      <span className="font-medium text-[color:var(--color-ink-900)]">
                        {notificationTitle(notification)}
                      </span>
                      <span className="text-xs text-[color:var(--color-ink-500)]">
                        {new Date(notification.created_at).toLocaleString()}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="border-t border-[color:var(--color-border)] px-3 py-2">
            <a
              href="/settings/notifications"
              className={cn(
                "text-xs text-[color:var(--color-accent)] underline-offset-4 hover:underline",
              )}
            >
              Notification settings
            </a>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function BellIcon() {
  // Inline SVG — не тащим иконочный пакет ради одной иконки.
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}
