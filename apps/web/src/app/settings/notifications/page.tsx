"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Checkbox } from "@/components/ui/checkbox";
import { type PreferenceItem, fetchPreferences, updatePreference } from "@/lib/notifications-api";

/**
 * Settings → Notifications (Phase 8.0).
 *
 * Per-event toggle. Channel-level toggles (in_app vs log) пока MVP не
 * нужны — только два внутренних канала, юзер не различает их UX-wise.
 * Phase 8.1 добавит email — там уже будет смысл «получать в email,
 * но не в in_app».
 *
 * Auth: пока mock через DEFAULT_USER_ID.
 */
export default function NotificationSettingsPage() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
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
      <h1 className="text-2xl font-semibold tracking-tight">Notification settings</h1>
      <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">
        Choose which events you want to be notified about. Disabled events won&rsquo;t appear in the
        bell or generate any record.
      </p>

      <div className="mt-8">
        {isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>
        ) : isError || !data ? (
          <p className="text-sm text-red-600">Failed to load preferences.</p>
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
  // Уникальный id для htmlFor — biome требует ассоциации label↔input.
  const inputId = `pref-${item.event_type}`;
  return (
    <li className="flex items-center justify-between py-3">
      <div>
        <div className="text-sm font-medium text-[color:var(--color-ink-900)]">
          {humanizeEventType(item.event_type)}
        </div>
        <div className="text-xs text-[color:var(--color-ink-500)]">
          {item.event_type}
          {item.is_default ? " · using default" : ""}
        </div>
      </div>
      <label htmlFor={inputId} className="flex items-center gap-2">
        <span className="text-xs text-[color:var(--color-ink-500)]">
          {item.enabled ? "Enabled" : "Disabled"}
        </span>
        <Checkbox
          id={inputId}
          aria-label={`Toggle ${item.event_type}`}
          checked={item.enabled}
          disabled={pending}
          onChange={(e) => onToggle(e.target.checked)}
        />
      </label>
    </li>
  );
}

function humanizeEventType(eventType: string): string {
  // Простая mapping-table — без i18n. Phase 4.1 i18n заменит.
  switch (eventType) {
    case "hypothesis_pending_review":
      return "New hypothesis to review";
    case "dna_match_found":
      return "New DNA match";
    case "import_completed":
      return "Import completed";
    case "import_failed":
      return "Import failed";
    case "merge_undone":
      return "Merge undone";
    case "dedup_suggestion_new":
      return "New duplicate suggestion";
    default:
      return eventType;
  }
}
