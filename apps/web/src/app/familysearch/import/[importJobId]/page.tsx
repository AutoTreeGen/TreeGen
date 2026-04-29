"use client";

/**
 * /familysearch/import/[importJobId] — live-прогресс FS-импорта (Phase 5.1).
 *
 * Подписывается на тот же SSE-канал, что GED-импорт (`/imports/{id}/events`),
 * но семантика этапов разная: для FS импорта — preview → fetch → persist.
 * Используется один общий ``ImportJobProgressEvent``-формат — backend
 * публикует ``stage`` совместимо с GED-pipeline'ом (PARSING на старте,
 * FINALIZING на финале), что переиспользует UI-прогрессбар.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ApiError, fetchImport, importEventsUrl } from "@/lib/api";
import {
  type ImportJobProgressEvent,
  type ImportStage,
  isTerminalStage,
  useEventSource,
} from "@/lib/sse";

const STAGE_LABELS: Record<ImportStage, string> = {
  QUEUED: "Queued",
  PARSING: "Fetching FamilySearch pedigree",
  ENTITIES: "Importing persons",
  FAMILIES: "Importing families",
  EVENTS: "Importing events",
  SOURCES: "Importing sources",
  FINALIZING: "Finalizing",
  SUCCEEDED: "Done",
  FAILED: "Failed",
  CANCELED: "Canceled",
};

export default function FamilySearchImportPage() {
  const t = useTranslations("familysearch.importStatus");
  const params = useParams<{ importJobId: string }>();
  const router = useRouter();
  const importJobId = params.importJobId;
  const startedAtRef = useRef<number>(Date.now());

  const job = useQuery({
    queryKey: ["import-job", importJobId],
    queryFn: () => fetchImport(importJobId),
    enabled: Boolean(importJobId),
    refetchInterval: 5000,
    refetchOnWindowFocus: false,
  });

  const sseUrl = importJobId ? importEventsUrl(importJobId) : null;
  const sse = useEventSource<ImportJobProgressEvent>(sseUrl);
  const event = sse.data;

  const stage: ImportStage = event?.stage ?? "QUEUED";
  const isTerminal = isTerminalStage(stage);
  const done = event?.done ?? 0;
  const total = event?.total ?? 0;
  const percent = total > 0 ? Math.min(100, (done / total) * 100) : null;

  const eta = useMemo(() => {
    if (isTerminal) return null;
    if (total <= 0 || done <= 0) return null;
    const elapsed = Date.now() - startedAtRef.current;
    if (elapsed < 1000) return null;
    const ratePerMs = done / elapsed;
    if (ratePerMs <= 0) return null;
    const remaining = (total - done) / ratePerMs;
    return formatDuration(remaining);
  }, [isTerminal, done, total]);

  // Терминальный SUCCEEDED → редирект на дерево, чтобы юзер сразу
  // увидел импортированных персон.
  useEffect(() => {
    if (stage === "SUCCEEDED" && job.data) {
      const timer = setTimeout(() => {
        router.push(`/trees/${job.data.tree_id}/persons`);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [stage, job.data, router]);

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/familysearch/connect">← FamilySearch</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Job <span className="font-mono">{importJobId}</span>
        </p>
      </header>

      {stage === "FAILED" ? (
        <Card className="border-red-200 ring-red-200">
          <CardHeader>
            <CardTitle>{t("importFailed")}</CardTitle>
            <CardDescription>
              {job.data?.error ?? "The worker reported a failure. Check server logs for details."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-end">
            <Button variant="primary" size="sm" asChild>
              <Link href="/familysearch/connect">{t("tryAgain")}</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{STAGE_LABELS[stage]}</CardTitle>
            <CardDescription>
              {sse.error && !isTerminal
                ? `Live updates disconnected (${sse.retries} retries). The import is still running on the server.`
                : "Live progress streamed via Server-Sent Events."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Progress value={percent} ariaLabel={`Import progress: ${STAGE_LABELS[stage]}`} />

            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                  Stage
                </dt>
                <dd className="font-mono">{stage}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                  Progress
                </dt>
                <dd>
                  {total > 0 ? (
                    <>
                      {done.toLocaleString("en-US")} / {total.toLocaleString("en-US")}
                    </>
                  ) : (
                    <span className="text-[color:var(--color-ink-500)]">awaiting first event…</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                  ETA
                </dt>
                <dd>{eta ?? <span className="text-[color:var(--color-ink-500)]">—</span>}</dd>
              </div>
            </dl>

            {job.isError ? (
              <p className="text-sm text-red-800" role="alert">
                {job.error instanceof ApiError ? job.error.message : "Failed to read job state."}
              </p>
            ) : null}

            {stage === "SUCCEEDED" ? (
              <p className="text-sm text-emerald-800">
                Import completed. Redirecting to persons list…
              </p>
            ) : null}
          </CardContent>
        </Card>
      )}
    </main>
  );
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${remMinutes}m`;
}
