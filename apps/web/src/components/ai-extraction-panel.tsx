"use client";

/**
 * Phase 10.2b — AI source extraction panel.
 *
 * Один self-contained client-component, который встраивается в source
 * detail page. Показывает:
 * - Текущий status последнего extraction'а (poll каждые 5 сек если PENDING).
 * - Cost / remaining budget badge.
 * - Image upload form для vision-extraction'а.
 * - Inline error messages при 429 / 422 / 415.
 *
 * После успешной vision-extraction'и компонент инвалидирует
 * `ai-extract-status` query и перезаписывает локальные cost-bar'ы свежим
 * snapshot'ом из response'а.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, fetchAIExtractStatus, postAIExtractVision } from "@/lib/api";

const ACCEPTED_MIME_TYPES = "image/jpeg,image/png,image/gif,image/webp";

export function AIExtractionPanel({ sourceId }: { sourceId: string }) {
  const t = useTranslations("aiExtraction");
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [chosenFile, setChosenFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = useQuery({
    queryKey: ["ai-extract-status", sourceId],
    queryFn: () => fetchAIExtractStatus(sourceId),
    refetchInterval: (query) => {
      // PENDING сейчас редко встречается (sync-mode); polling включается
      // только когда status действительно PENDING — иначе 0 traffic.
      const lastStatus = query.state.data?.extraction?.status;
      return lastStatus === "pending" ? 5000 : false;
    },
  });

  const extractMutation = useMutation({
    mutationFn: (file: File) => postAIExtractVision(sourceId, file),
    onSuccess: () => {
      setError(null);
      setChosenFile(null);
      void queryClient.invalidateQueries({ queryKey: ["ai-extract-status", sourceId] });
    },
    onError: (err) => {
      setError(formatError(err, t));
    },
  });

  const onFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setChosenFile(file);
    setError(null);
  };

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!chosenFile) return;
    extractMutation.mutate(chosenFile);
  };

  const data = status.data;

  return (
    <Card data-testid="ai-extraction-panel">
      <CardHeader>
        <CardTitle>{t("panelTitle")}</CardTitle>
        <CardDescription>{t("panelDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <StatusBlock status={status.data} t={t} />

        <BudgetBlock data={data ?? null} t={t} />

        <form onSubmit={onSubmit} className="space-y-3" aria-label={t("uploadHeading")}>
          <div>
            <h3 className="text-sm font-medium">{t("uploadHeading")}</h3>
            <p className="text-xs text-[color:var(--color-ink-500)]">{t("uploadDescription")}</p>
          </div>

          <div className="flex items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_MIME_TYPES}
              onChange={onFileChange}
              className="sr-only"
              data-testid="ai-extract-file-input"
              aria-label={t("chooseFileButton")}
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
            >
              {t("chooseFileButton")}
            </Button>
            <span className="truncate text-xs text-[color:var(--color-ink-500)]">
              {chosenFile?.name ?? t("noFileChosen")}
            </span>
          </div>

          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={!chosenFile || extractMutation.isPending}
            data-testid="ai-extract-submit"
          >
            {extractMutation.isPending ? t("submitting") : t("submit")}
          </Button>

          {extractMutation.data ? (
            <ImageOptimizationNotice result={extractMutation.data} t={t} />
          ) : null}

          {error ? (
            <p className="text-sm text-red-800" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      </CardContent>
    </Card>
  );
}

function StatusBlock({
  status,
  t,
}: {
  status: import("@/lib/api").AIExtractStatusResponse | undefined;
  t: ReturnType<typeof useTranslations>;
}) {
  if (!status) {
    return null;
  }
  const heading = <h3 className="text-sm font-medium">{t("statusHeading")}</h3>;
  if (!status.has_extraction || !status.extraction) {
    return (
      <div className="space-y-1">
        {heading}
        <p className="text-sm text-[color:var(--color-ink-500)]">{t("statusNone")}</p>
      </div>
    );
  }
  const { extraction } = status;
  const variant: "neutral" | "accent" = extraction.status === "completed" ? "accent" : "neutral";
  const label =
    extraction.status === "completed"
      ? t("statusCompleted", { facts: status.fact_count })
      : extraction.status === "failed"
        ? t("statusFailed")
        : t("statusPending");
  return (
    <div className="space-y-1">
      {heading}
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={variant}>{label}</Badge>
        <span className="text-xs text-[color:var(--color-ink-500)]">
          {t("costLabel")}: ${status.cost_usd.toFixed(4)}
        </span>
      </div>
    </div>
  );
}

function BudgetBlock({
  data,
  t,
}: {
  data: import("@/lib/api").AIExtractStatusResponse | null;
  t: ReturnType<typeof useTranslations>;
}) {
  if (!data) return null;
  const cap = data.extract_budget_usd;
  const lastCost = data.cost_usd;
  const remaining = cap > 0 ? Math.max(0, cap - lastCost) : 0;
  return (
    <div
      className="flex flex-wrap gap-3 text-xs text-[color:var(--color-ink-500)]"
      data-testid="ai-extract-budget"
    >
      {cap > 0 ? (
        <span>
          {t("costRemaining", {
            remaining: `$${remaining.toFixed(2)}`,
            cap: `$${cap.toFixed(2)}`,
          })}
        </span>
      ) : null}
      {data.budget_remaining_runs >= 0 ? (
        <span>{t("budgetRunsRemaining", { remaining: data.budget_remaining_runs })}</span>
      ) : null}
      {data.budget_remaining_tokens >= 0 ? (
        <span>{t("budgetTokensRemaining", { remaining: data.budget_remaining_tokens })}</span>
      ) : null}
    </div>
  );
}

function ImageOptimizationNotice({
  result,
  t,
}: {
  result: import("@/lib/api").AIExtractVisionResponse;
  t: ReturnType<typeof useTranslations>;
}) {
  const messages: string[] = [];
  if (result.image_was_resized) messages.push(t("imageResized"));
  if (result.image_was_rotated) messages.push(t("imageRotated"));
  if (result.image_original_bytes && result.image_processed_bytes) {
    messages.push(
      t("imageOptimized", {
        originalKb: Math.round(result.image_original_bytes / 1024),
        processedKb: Math.round(result.image_processed_bytes / 1024),
      }),
    );
  }
  if (messages.length === 0) return null;
  return (
    <p className="text-xs text-[color:var(--color-ink-500)]" data-testid="ai-extract-image-notice">
      {messages.join(" · ")}
    </p>
  );
}

function formatError(err: unknown, t: ReturnType<typeof useTranslations>): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      // Backend кладёт detail в JSON object с limit_kind. Сообщение мы берём
      // из ApiError.message (TypeScript-generic-cast не нужен — message
      // уже строка).
      return t("errorBudget", { kind: err.message });
    }
    if (err.status === 415 || err.status === 422 || err.status === 413) {
      return t("errorVisionRejected");
    }
  }
  return t("errorGeneric");
}
