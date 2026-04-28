"use client";

/**
 * Inline-панель bulk hypothesis-compute (Phase 7.5 finalize).
 *
 * Состояния:
 * - idle: только кнопка «Compute all hypotheses» в header странице.
 * - active: ProgressPanel с текущей стадией, прогресс-баром, cancel-кнопкой.
 * - terminal (succeeded): success-баннер + кнопка «Dismiss» → сброс в idle
 *   и invalidate query списка hypotheses (caller отвечает через onCompleted).
 * - terminal (failed | cancelled): описание + Reset.
 *
 * SSE через ``useEventSource<BulkComputeProgressEvent>(events_url)``.
 * Пока соединение не пришло — показываем cached progress с POST-ответа
 * (status/processed/total из БД), чтобы не моргать.
 */

import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  ApiError,
  type HypothesisComputeJobResponse,
  bulkComputeEventsUrl,
  cancelBulkComputeJob,
  startBulkCompute,
} from "@/lib/api";
import {
  type BulkComputeProgressEvent,
  type BulkComputeStage,
  isBulkComputeTerminalStage,
  useEventSource,
} from "@/lib/sse";

const STAGE_LABELS: Record<BulkComputeStage, string> = {
  loading_rules: "Loading rule registry",
  iterating_pairs: "Iterating person pairs",
  persisting: "Persisting hypotheses",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export type BulkComputePanelProps = {
  treeId: string;
  /** Вызывается когда job успешно завершился — для invalidate'а списка. */
  onCompleted?: () => void;
};

export function BulkComputePanel({ treeId, onCompleted }: BulkComputePanelProps) {
  const [activeJob, setActiveJob] = useState<HypothesisComputeJobResponse | null>(null);
  const [completedNotice, setCompletedNotice] = useState<{
    created: number;
    total: number;
  } | null>(null);

  const start = useMutation({
    mutationFn: () => startBulkCompute(treeId),
    onSuccess: (job) => {
      setActiveJob(job);
      setCompletedNotice(null);
    },
  });

  const cancel = useMutation({
    mutationFn: () => {
      if (!activeJob) throw new Error("No active job to cancel");
      return cancelBulkComputeJob(activeJob.id);
    },
    onSuccess: (job) => {
      setActiveJob(job);
    },
  });

  // SSE: подключаемся только когда есть active job c events_url.
  // Inline-режим backend'а возвращает 201 без events_url — на этом
  // ветке job уже terminal, SSE не нужен.
  const sseUrl = activeJob?.events_url ? bulkComputeEventsUrl(treeId, activeJob.id) : null;
  const sse = useEventSource<BulkComputeProgressEvent>(sseUrl);
  const event = sse.data;

  // Cтадия: SSE > job-ответ (когда первого фрейма ещё нет, рендерим
  // снапшот из БД). Inline-режим — статус из job напрямую.
  const stage: BulkComputeStage =
    event?.stage ?? (activeJob ? statusToStage(activeJob.status) : "loading_rules");
  const isTerminal = isBulkComputeTerminalStage(stage);

  const processed = event?.current ?? activeJob?.progress.processed ?? 0;
  const total = event?.total ?? activeJob?.progress.total ?? 0;
  const percent = total > 0 ? Math.min(100, (processed / total) * 100) : null;

  const message =
    event?.message ??
    (stage === "iterating_pairs" && total > 0
      ? `Iterating person pairs (${processed}/${total})`
      : null);

  // На терминальный стейт: вытащить итоги из снапшота (job-row через GET
  // мы уже не дергаем — события из SSE достаточно), показать success-баннер
  // и сообщить наверх через onCompleted, чтобы caller инвалидировал
  // список hypotheses.
  useEffect(() => {
    if (stage !== "succeeded") return;
    setCompletedNotice({
      created: activeJob?.progress.hypotheses_created ?? 0,
      total,
    });
    onCompleted?.();
  }, [stage, activeJob, total, onCompleted]);

  const reset = () => {
    setActiveJob(null);
    setCompletedNotice(null);
    start.reset();
    cancel.reset();
  };

  // ----- Render branches -----

  if (!activeJob) {
    return (
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Button
          variant="primary"
          size="md"
          onClick={() => start.mutate()}
          disabled={start.isPending}
        >
          {start.isPending ? "Starting…" : "Compute all hypotheses"}
        </Button>
        {start.error ? (
          <p role="alert" className="text-sm text-red-700" data-testid="bulk-compute-error">
            {formatError(start.error)}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <section
      aria-label="Bulk compute progress"
      data-testid="bulk-compute-panel"
      className="flex flex-col gap-3 rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
            Bulk hypothesis compute
          </p>
          <h3 className="text-sm font-semibold">{STAGE_LABELS[stage]}</h3>
        </div>
        <div className="flex gap-2">
          {!isTerminal ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending || activeJob.cancel_requested}
            >
              {cancel.isPending
                ? "Cancelling…"
                : activeJob.cancel_requested
                  ? "Cancel requested"
                  : "Cancel"}
            </Button>
          ) : (
            <Button variant="ghost" size="sm" onClick={reset}>
              Dismiss
            </Button>
          )}
        </div>
      </header>

      <Progress value={percent} ariaLabel={`Bulk compute progress: ${STAGE_LABELS[stage]}`} />

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
        <div>
          <dt className="uppercase tracking-wide text-[color:var(--color-ink-500)]">Stage</dt>
          <dd className="font-mono">{stage}</dd>
        </div>
        <div>
          <dt className="uppercase tracking-wide text-[color:var(--color-ink-500)]">Pairs</dt>
          <dd>
            {total > 0
              ? `${processed.toLocaleString("en-US")} / ${total.toLocaleString("en-US")}`
              : "—"}
          </dd>
        </div>
        <div>
          <dt className="uppercase tracking-wide text-[color:var(--color-ink-500)]">Job</dt>
          <dd className="font-mono">{activeJob.id.slice(0, 8)}…</dd>
        </div>
      </dl>

      {message ? <p className="text-xs text-[color:var(--color-ink-500)]">{message}</p> : null}

      {stage === "succeeded" && completedNotice ? (
        <output
          data-testid="bulk-compute-success"
          className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900"
        >
          Created {completedNotice.created.toLocaleString("en-US")} hypotheses across{" "}
          {completedNotice.total.toLocaleString("en-US")} candidate pairs.
        </output>
      ) : null}

      {stage === "failed" ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
        >
          {activeJob.error ?? message ?? "Bulk compute failed; see server logs."}
        </p>
      ) : null}

      {stage === "cancelled" ? (
        <p className="text-sm text-[color:var(--color-ink-700)]">
          Bulk compute was cancelled before completion.
        </p>
      ) : null}

      {sse.error && !isTerminal ? (
        <p className="text-xs text-amber-800">
          Live updates disconnected ({sse.retries} retries). The job is still running on the server.
        </p>
      ) : null}

      {cancel.error ? (
        <p role="alert" className="text-xs text-red-700">
          {formatError(cancel.error)}
        </p>
      ) : null}
    </section>
  );
}

/** Маппинг status БД-job'а в SSE-стадию (для рендеринга до первого фрейма). */
function statusToStage(status: HypothesisComputeJobResponse["status"]): BulkComputeStage {
  switch (status) {
    case "queued":
    case "running":
      return "loading_rules";
    case "succeeded":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
  }
}

function formatError(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
