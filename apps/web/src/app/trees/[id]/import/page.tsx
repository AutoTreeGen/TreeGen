"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  ApiError,
  type ImportJobResponse,
  cancelImport,
  importEventsUrl,
  postImport,
} from "@/lib/api";
import {
  type ImportJobProgressEvent,
  type ImportStage,
  isTerminalStage,
  useEventSource,
} from "@/lib/sse";

/**
 * Человеко-понятные ярлыки для UI. Порядок ключей повторяет реальный
 * порядок стадий ``ImportRunner`` — нужен для фолбэк-индикатора, пока
 * первый прогресс-кадр не пришёл.
 */
const STAGE_LABELS: Record<ImportStage, string> = {
  QUEUED: "Queued",
  PARSING: "Parsing GEDCOM",
  ENTITIES: "Importing persons",
  FAMILIES: "Importing families",
  EVENTS: "Importing events",
  SOURCES: "Importing sources",
  FINALIZING: "Finalizing",
  SUCCEEDED: "Done",
  FAILED: "Failed",
  CANCELED: "Canceled",
};

export default function ImportPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const treeId = params.id;

  const [file, setFile] = useState<File | null>(null);
  const [activeJob, setActiveJob] = useState<ImportJobResponse | null>(null);
  const startedAtRef = useRef<number | null>(null);

  const upload = useMutation({
    mutationFn: postImport,
    onSuccess: (job) => {
      setActiveJob(job);
      startedAtRef.current = Date.now();
    },
  });

  // Subscribe to SSE while the job is in-flight. URL=null отключает
  // соединение, поэтому до первого uploadа хук не пытается коннектиться.
  const sseUrl = activeJob ? importEventsUrl(activeJob.id) : null;
  const sse = useEventSource<ImportJobProgressEvent>(sseUrl);
  const event = sse.data;

  const cancel = useMutation({
    mutationFn: () => {
      if (!activeJob) throw new Error("No active job to cancel");
      return cancelImport(activeJob.id);
    },
    onSuccess: (job) => {
      setActiveJob(job);
    },
  });

  const stage: ImportStage = event?.stage ?? "QUEUED";
  const isTerminal = isTerminalStage(stage);
  const done = event?.done ?? 0;
  const total = event?.total ?? 0;
  const percent = total > 0 ? Math.min(100, (done / total) * 100) : null;
  const eta = useEta(stage, done, total, startedAtRef.current);

  // Терминальные переходы: SUCCEEDED → редирект, FAILED → ничего не
  // делаем (карточка ошибки уже видна), CANCELED — пользователь сам решит.
  useEffect(() => {
    if (stage === "SUCCEEDED" && activeJob) {
      const timer = setTimeout(() => {
        router.push(`/trees/${activeJob.tree_id}/persons`);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [stage, activeJob, router]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    upload.mutate(file);
  };

  const reset = () => {
    setActiveJob(null);
    setFile(null);
    startedAtRef.current = null;
    upload.reset();
    cancel.reset();
  };

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← Back to persons</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Import GEDCOM</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Upload a .ged or .gedcom file. The parser runs in the background; this page subscribes to
          live progress updates.
        </p>
      </header>

      {!activeJob ? (
        <UploadForm
          file={file}
          isUploading={upload.isPending}
          error={upload.error}
          onFileChange={setFile}
          onSubmit={handleSubmit}
        />
      ) : (
        <ProgressCard
          job={activeJob}
          stage={stage}
          done={done}
          total={total}
          percent={percent}
          eta={eta}
          isTerminal={isTerminal}
          isCancelling={cancel.isPending}
          onCancel={() => cancel.mutate()}
          onReset={reset}
          sseError={sse.error}
          retries={sse.retries}
        />
      )}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

function UploadForm({
  file,
  isUploading,
  error,
  onFileChange,
  onSubmit,
}: {
  file: File | null;
  isUploading: boolean;
  error: unknown;
  onFileChange: (file: File | null) => void;
  onSubmit: (e: FormEvent) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>New import</CardTitle>
        <CardDescription>
          Files are limited to the parser-service&apos;s configured upload size (default
          ~150&nbsp;MB).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="import-file"
              className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
            >
              GEDCOM file
            </label>
            <Input
              id="import-file"
              type="file"
              accept=".ged,.gedcom"
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <p className="text-xs text-[color:var(--color-ink-500)]">
                Selected: <span className="font-mono">{file.name}</span> (
                {(file.size / 1024 / 1024).toFixed(2)} MB)
              </p>
            ) : null}
          </div>

          {error ? <UploadError error={error} /> : null}

          <div className="flex justify-end">
            <Button type="submit" variant="primary" size="md" disabled={!file || isUploading}>
              {isUploading ? "Uploading…" : "Start import"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function UploadError({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
    >
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Progress card
// ---------------------------------------------------------------------------

function ProgressCard({
  job,
  stage,
  done,
  total,
  percent,
  eta,
  isTerminal,
  isCancelling,
  onCancel,
  onReset,
  sseError,
  retries,
}: {
  job: ImportJobResponse;
  stage: ImportStage;
  done: number;
  total: number;
  percent: number | null;
  eta: string | null;
  isTerminal: boolean;
  isCancelling: boolean;
  onCancel: () => void;
  onReset: () => void;
  sseError: Error | null;
  retries: number;
}) {
  if (stage === "FAILED") {
    return (
      <Card className="border-red-200 ring-red-200">
        <CardHeader>
          <CardTitle>Import failed</CardTitle>
          <CardDescription>
            {job.error ?? "The import worker reported a failure. See server logs for details."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-end">
          <Button variant="primary" size="sm" onClick={onReset}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{STAGE_LABELS[stage]}</CardTitle>
        <CardDescription>
          Job <span className="font-mono">{job.id}</span>
          {job.source_filename ? <> · {job.source_filename}</> : null}
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

        {stage === "SUCCEEDED" ? (
          <p className="text-sm text-emerald-800">Import completed. Redirecting to persons list…</p>
        ) : null}

        {stage === "CANCELED" ? (
          <p className="text-sm text-[color:var(--color-ink-700)]">
            Import was canceled before completion.
          </p>
        ) : null}

        {sseError && !isTerminal ? (
          <p className="text-sm text-amber-800">
            Live updates disconnected ({retries} retries). The import is still running on the
            server.
          </p>
        ) : null}
      </CardContent>
      <CardContent className="flex justify-end gap-2 pt-0">
        {!isTerminal ? (
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={isCancelling}>
            {isCancelling ? "Canceling…" : "Cancel"}
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={onReset}>
            Start another
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ETA helper
// ---------------------------------------------------------------------------

/**
 * Линейная экстраполяция оставшегося времени из текущего темпа. Если у
 * нас нет ни ``total>0``, ни прошедшего времени — отдаём null.
 *
 * Намеренно простая модель: точное прогнозирование требует учёта стадий
 * (parse vs bulk insert), исторических данных и т.п. Phase 13.
 */
function useEta(
  stage: ImportStage,
  done: number,
  total: number,
  startedAt: number | null,
): string | null {
  return useMemo(() => {
    if (stage === "SUCCEEDED" || stage === "FAILED" || stage === "CANCELED") return null;
    if (!startedAt || total <= 0 || done <= 0) return null;
    const elapsedMs = Date.now() - startedAt;
    if (elapsedMs < 1_000) return null;
    const ratePerMs = done / elapsedMs;
    if (ratePerMs <= 0) return null;
    const remainingMs = (total - done) / ratePerMs;
    return formatDuration(remainingMs);
  }, [stage, done, total, startedAt]);
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
