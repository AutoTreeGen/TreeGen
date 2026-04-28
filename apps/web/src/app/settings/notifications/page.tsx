"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { ErrorMessage } from "@/components/error-message";
import { Checkbox } from "@/components/ui/checkbox";
import { type PreferenceItem, fetchPreferences, updatePreference } from "@/lib/notifications-api";

/**
 * Settings → Notifications (Phase 8.0).
 * Phase 4.13: все строки в `notifications.*` namespace, ошибки — через
 * `<ErrorMessage code=...>` (`errors.preferencesLoadFailed`).
 *
 * Auth: пока mock через DEFAULT_USER_ID.
 */
export default function NotificationSettingsPage() {
  const t = useTranslations("notifications");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => fetchPreferences(),
  });

  const toggle = useMutation({
    mutationFn: ({ eventType, enabled }: { eventType: string; enabled: boolean }) =>
      updatePreference(eventType, { enabled }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
    },
  });

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">{t("settingsTitle")}</h1>
      <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">{t("settingsDescription")}</p>

      <div className="mt-8">
        {isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{tCommon("loading")}</p>
        ) : isError || !data ? (
          <ErrorMessage code="preferencesLoadFailed" onRetry={() => void refetch()} />
        ) : (
          <ul className="divide-y divide-[color:var(--color-border)]">
            {data.items.map((item) => (
              <PreferenceRow
                key={item.event_type}
                item={item}
                onToggle={(enabled) => toggle.mutate({ eventType: item.event_type, enabled })}
                pending={toggle.isPending && toggle.variables?.eventType === item.event_type}
              />
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

function PreferenceRow({
  item,
  onToggle,
  pending,
}: {
  item: PreferenceItem;
  onToggle: (enabled: boolean) => void;
  pending: boolean;
}) {
  const t = useTranslations("notifications");
  const tCommon = useTranslations("common");
  // Уникальный id для htmlFor — biome требует ассоциации label↔input.
  const inputId = `pref-${item.event_type}`;
  const eventTitle = humanizeEventType(t, item.event_type);
  return (
    <li className="flex items-center justify-between py-3">
      <div>
        <div className="text-sm font-medium text-[color:var(--color-ink-900)]">{eventTitle}</div>
        <div className="text-xs text-[color:var(--color-ink-500)]">
          {item.event_type}
          {item.is_default ? ` · ${t("usingDefault")}` : ""}
        </div>
      </div>
      <label htmlFor={inputId} className="flex items-center gap-2">
        <span className="text-xs text-[color:var(--color-ink-500)]">
          {item.enabled ? tCommon("enabled") : tCommon("disabled")}
        </span>
        <Checkbox
          id={inputId}
          aria-label={t("toggleAria", { event: eventTitle })}
          checked={item.enabled}
          disabled={pending}
          onChange={(e) => onToggle(e.target.checked)}
        />
      </label>
    </li>
  );
}

/**
 * Резолвит человекочитаемое имя event-типа из `notifications.events.*`.
 * Незнакомый код → возвращаем сам код (defensive: backend может прислать
 * новый event_type до того, как messages обновятся).
 */
function humanizeEventType(t: ReturnType<typeof useTranslations>, eventType: string): string {
  // next-intl бросит, если ключа нет — глушим try/catch и фоллбечимся
  // на сам event_type, чтобы UI не падал на новых типах.
  try {
    return t(`events.${eventType}` as never);
  } catch {
    return eventType;
  }
}
