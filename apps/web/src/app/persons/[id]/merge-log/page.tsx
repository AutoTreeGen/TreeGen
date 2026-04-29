"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  MERGE_UNDO_WINDOW_DAYS,
  type MergeHistoryItem,
  fetchMergeHistory,
  isMergeUndoable,
  undoMerge,
} from "@/lib/api";

/**
 * Phase 6.4 — merge history + undo UI.
 *
 * Маршрут: ``/persons/{id}/merge-log``. Показывает все merge'и, в которых
 * этот person участвовал (как survivor или merged), с кнопкой undo для тех,
 * что в 90-дневном окне (ADR-0022 §undo policy).
 *
 * Server остаётся source of truth: вернёт 410 ``undo_window_expired`` если
 * клиентский расчёт промахнулся (clock skew, race с purge-cron'ом). Тогда
 * показываем ошибку, скрываем кнопку.
 */
export default function MergeLogPage() {
  const t = useTranslations("persons.merge.log");
  const params = useParams<{ id: string }>();
  const personId = params.id;
  const queryClient = useQueryClient();

  const historyQuery = useQuery({
    queryKey: ["merge-history", personId],
    queryFn: () => fetchMergeHistory(personId),
    enabled: Boolean(personId),
  });

  const undoMutation = useMutation({
    mutationFn: (mergeId: string) => undoMerge(mergeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["merge-history", personId] });
      queryClient.invalidateQueries({ queryKey: ["person", personId] });
    },
  });

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/persons/${personId}`}>← {t("backToPerson")}</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--color-ink-500)]">
          {t("subtitle", { days: MERGE_UNDO_WINDOW_DAYS })}
        </p>
      </header>

      {historyQuery.isLoading ? <HistorySkeleton /> : null}

      {historyQuery.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("loadError")}</CardTitle>
            <CardDescription>
              {historyQuery.error instanceof ApiError
                ? `${historyQuery.error.status}: ${historyQuery.error.message}`
                : (historyQuery.error as Error)?.message}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="primary" size="sm" onClick={() => historyQuery.refetch()}>
              {t("retry")}
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {historyQuery.data ? (
        historyQuery.data.items.length === 0 ? (
          <p className="rounded-md bg-[color:var(--color-surface-muted)] px-3 py-2 text-sm text-[color:var(--color-ink-500)]">
            {t("empty")}
          </p>
        ) : (
          <ul className="space-y-3" data-testid="merge-log-list">
            {historyQuery.data.items.map((item) => (
              <MergeLogItem
                key={item.merge_id}
                item={item}
                personId={personId}
                onUndo={() => undoMutation.mutate(item.merge_id)}
                undoing={undoMutation.isPending && undoMutation.variables === item.merge_id}
                undoError={
                  undoMutation.variables === item.merge_id && undoMutation.error
                    ? undoMutation.error
                    : null
                }
              />
            ))}
          </ul>
        )
      ) : null}
    </main>
  );
}

function MergeLogItem({
  item,
  personId,
  onUndo,
  undoing,
  undoError,
}: {
  item: MergeHistoryItem;
  personId: string;
  onUndo: () => void;
  undoing: boolean;
  undoError: unknown;
}) {
  const t = useTranslations("persons.merge.log");
  const role = item.survivor_id === personId ? "survivor" : "merged";
  const undoable = isMergeUndoable(item);
  const formattedMergedAt = formatTimestamp(item.merged_at);
  const formattedUndoneAt = item.undone_at ? formatTimestamp(item.undone_at) : null;
  const formattedPurgedAt = item.purged_at ? formatTimestamp(item.purged_at) : null;

  return (
    <li
      data-testid="merge-log-item"
      className="rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <Badge variant={role === "survivor" ? "accent" : "neutral"}>
          {role === "survivor" ? t("roleSurvivor") : t("roleMerged")}
        </Badge>
        <span className="text-sm text-[color:var(--color-ink-700)]">
          {t("mergedAt", { time: formattedMergedAt })}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
        <p>
          <span className="text-[color:var(--color-ink-500)]">{t("survivorLabel")}:</span>{" "}
          <Link
            href={`/persons/${item.survivor_id}`}
            className="break-all font-mono text-[color:var(--color-accent)] hover:underline"
          >
            {item.survivor_id}
          </Link>
        </p>
        <p>
          <span className="text-[color:var(--color-ink-500)]">{t("mergedLabel")}:</span>{" "}
          <span className="break-all font-mono">{item.merged_id}</span>
        </p>
        <p className="md:col-span-2">
          <span className="text-[color:var(--color-ink-500)]">merge_id:</span>{" "}
          <span className="break-all font-mono">{item.merge_id}</span>
        </p>
      </div>

      {formattedUndoneAt ? (
        <p className="mt-3 rounded bg-[color:var(--color-surface-muted)] px-2 py-1 text-xs text-[color:var(--color-ink-700)]">
          {t("undoneAt", { time: formattedUndoneAt })}
        </p>
      ) : null}

      {formattedPurgedAt ? (
        <p className="mt-3 rounded bg-red-50 px-2 py-1 text-xs text-red-900 ring-1 ring-red-100">
          {t("purgedAt", { time: formattedPurgedAt })}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          size="sm"
          onClick={onUndo}
          disabled={!undoable || undoing}
          data-testid="undo-button"
          aria-label={t("undoAria")}
        >
          {undoing ? t("undoing") : t("undoButton")}
        </Button>
        {!undoable ? (
          <span className="text-xs text-[color:var(--color-ink-500)]">
            {item.undone_at
              ? t("undoUnavailableUndone")
              : item.purged_at
                ? t("undoUnavailablePurged")
                : t("undoUnavailableExpired", { days: MERGE_UNDO_WINDOW_DAYS })}
          </span>
        ) : null}
      </div>

      {undoError ? (
        <p className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-900 ring-1 ring-red-200">
          {formatUndoError(undoError, t)}
        </p>
      ) : null}
    </li>
  );
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function formatUndoError(
  error: unknown,
  t: ReturnType<typeof useTranslations<"persons.merge.log">>,
): string {
  if (error instanceof ApiError) {
    if (error.status === 410) return t("undoErrorExpired");
    if (error.status === 409) return t("undoErrorAlreadyUndone");
    return `${error.status}: ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return t("undoErrorUnknown");
}

function HistorySkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}
