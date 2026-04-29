"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ConflictResolver, type ResolverSide } from "@/components/person-merge/conflict-resolver";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type MergeCommitResponse,
  type MergePreviewResponse,
  type SurvivorChoice,
  commitMerge,
  fetchMergePreview,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Phase 6.4 — manual person merge UI.
 *
 * Маршрут: ``/persons/merge/{primaryId}?candidate={candidateId}``.
 *
 * Отличается от Phase 4.6 ``/persons/{id}/merge/{targetId}`` тем, что
 * это **field-by-field** UI: для каждого conflicting поля рендерится
 * отдельный resolver с radio (left/right) и опциональной заметкой.
 * Бэкенд (ADR-0022) принимает только один ``survivor_choice`` на весь
 * merge — UI вычисляет implied survivor из мажоритарного choice
 * пользователя (см. ADR-0044).
 */
function makeConfirmToken(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

type FieldChoice = {
  side: ResolverSide | null;
  note: string;
};

export default function ManualMergePage() {
  const t = useTranslations("persons.merge");
  const router = useRouter();
  const params = useParams<{ primaryId: string }>();
  const searchParams = useSearchParams();
  const primaryId = params.primaryId;
  const candidateId = searchParams.get("candidate");

  const [choices, setChoices] = useState<Record<string, FieldChoice>>({});
  const [reviewed, setReviewed] = useState(false);
  const [confirmToken] = useState(makeConfirmToken);
  const [committed, setCommitted] = useState<MergeCommitResponse | null>(null);

  const previewQuery = useQuery({
    queryKey: ["manual-merge-preview", primaryId, candidateId],
    queryFn: () => {
      if (!candidateId) throw new Error("missing candidate query param");
      return fetchMergePreview(primaryId, {
        target_id: candidateId,
        survivor_choice: null,
        confirm_token: confirmToken,
      });
    },
    enabled: Boolean(primaryId && candidateId) && committed === null,
  });

  const preview = previewQuery.data;
  const blocked = (preview?.conflicts.length ?? 0) > 0;

  const impliedSurvivorChoice = useMemo<SurvivorChoice | null>(
    () => computeImpliedSurvivor(preview, choices, primaryId),
    [preview, choices, primaryId],
  );

  const commitMutation = useMutation({
    mutationFn: () => {
      if (!candidateId) throw new Error("missing candidate query param");
      return commitMerge(primaryId, {
        target_id: candidateId,
        confirm: true,
        confirm_token: confirmToken,
        survivor_choice: impliedSurvivorChoice,
      });
    },
    onSuccess: (data) => setCommitted(data),
  });

  function setSide(field: string, side: ResolverSide) {
    setChoices((prev) => ({
      ...prev,
      [field]: { side, note: prev[field]?.note ?? "" },
    }));
  }

  function setNote(field: string, note: string) {
    setChoices((prev) => ({
      ...prev,
      [field]: { side: prev[field]?.side ?? null, note },
    }));
  }

  if (!candidateId) {
    return <MissingCandidateState primaryId={primaryId} />;
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/persons/${primaryId}`}>← {t("backToPerson")}</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
      </header>

      {previewQuery.isLoading ? <MergeLoadingSkeleton /> : null}

      {previewQuery.isError ? (
        <MergeErrorState error={previewQuery.error} onRetry={() => previewQuery.refetch()} />
      ) : null}

      {committed ? (
        <MergeSuccess
          result={committed}
          onBack={() => router.push(`/persons/${committed.survivor_id}`)}
        />
      ) : null}

      {preview && committed === null ? (
        <>
          <SidesHeader
            primaryId={primaryId}
            candidateId={candidateId}
            preview={preview}
            impliedSurvivor={impliedSurvivorChoice}
          />

          <Separator className="my-6" />

          <FieldResolvers preview={preview} choices={choices} onSide={setSide} onNote={setNote} />

          <Separator className="my-6" />

          <PreviewPane
            preview={preview}
            primaryId={primaryId}
            candidateId={candidateId}
            choices={choices}
            impliedSurvivor={impliedSurvivorChoice}
          />

          <Separator className="my-6" />

          <ConflictBlock conflicts={preview.conflicts} />

          <Separator className="my-6" />

          <ConfirmDialog
            blocked={blocked}
            reviewed={reviewed}
            committing={commitMutation.isPending}
            commitError={commitMutation.error}
            onReviewedChange={setReviewed}
            onConfirm={() => commitMutation.mutate()}
            primaryId={primaryId}
            candidateId={candidateId}
            impliedSurvivor={impliedSurvivorChoice}
          />
        </>
      ) : null}
    </main>
  );
}

/**
 * Compute implied ``survivor_choice`` from per-field user picks.
 *
 * Backend (ADR-0022) поддерживает один-на-весь-merge survivor_choice.
 * UI собирает field-level intent — здесь сводим к мажоритарному
 * выбору. Tie / no choices → возвращаем ``null`` (default-выбор бэка).
 *
 * NB: если default_survivor_id == primaryId, то "left" выбран
 * пользователем = primary survives = ``"left"``. Если backend dynamically
 * меняет default, выбор пользователя всё равно применяется явно.
 */
function computeImpliedSurvivor(
  preview: MergePreviewResponse | undefined,
  choices: Record<string, FieldChoice>,
  primaryId: string,
): SurvivorChoice | null {
  if (!preview) return null;
  let leftCount = 0;
  let rightCount = 0;
  for (const choice of Object.values(choices)) {
    if (choice.side === "left") leftCount += 1;
    else if (choice.side === "right") rightCount += 1;
  }
  if (leftCount === 0 && rightCount === 0) return null;
  if (leftCount > rightCount) return "left";
  if (rightCount > leftCount) return "right";
  // Tie — даём приоритет default'у бэкенда.
  return preview.default_survivor_id === primaryId ? "left" : "right";
}

function SidesHeader({
  primaryId,
  candidateId,
  preview,
  impliedSurvivor,
}: {
  primaryId: string;
  candidateId: string;
  preview: MergePreviewResponse;
  impliedSurvivor: SurvivorChoice | null;
}) {
  const t = useTranslations("persons.merge");
  const defaultIsLeft = preview.default_survivor_id === primaryId;
  const leftIsSurvivor = impliedSurvivor === "left" || (impliedSurvivor === null && defaultIsLeft);
  const rightIsSurvivor = !leftIsSurvivor;

  return (
    <section aria-labelledby="sides-heading">
      <h2 id="sides-heading" className="mb-3 text-lg font-semibold">
        {t("sides.title")}
      </h2>
      <p className="mb-4 text-sm text-[color:var(--color-ink-500)]">{t("sides.body")}</p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <SideCard
          title={t("sides.primary")}
          subtitle={primaryId}
          isSurvivor={leftIsSurvivor}
          isDefault={defaultIsLeft}
        />
        <SideCard
          title={t("sides.candidate")}
          subtitle={candidateId}
          isSurvivor={rightIsSurvivor}
          isDefault={!defaultIsLeft}
        />
      </div>
    </section>
  );
}

function SideCard({
  title,
  subtitle,
  isSurvivor,
  isDefault,
}: {
  title: string;
  subtitle: string;
  isSurvivor: boolean;
  isDefault: boolean;
}) {
  const t = useTranslations("persons.merge.sides");
  return (
    <div
      className={cn(
        "rounded-lg border p-4",
        isSurvivor
          ? "border-[color:var(--color-accent)] bg-[color:var(--color-surface)] ring-1 ring-[color:var(--color-accent)]"
          : "border-[color:var(--color-border)] bg-[color:var(--color-surface)]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{title}</span>
        <div className="flex gap-1">
          {isSurvivor ? <Badge variant="accent">{t("survivorBadge")}</Badge> : null}
          {isDefault ? <Badge variant="neutral">{t("defaultBadge")}</Badge> : null}
        </div>
      </div>
      <p className="mt-1 break-all font-mono text-xs text-[color:var(--color-ink-500)]">
        {subtitle}
      </p>
    </div>
  );
}

const KNOWN_FIELD_LABEL_KEYS = new Set([
  "gedcom_xref",
  "sex",
  "confidence_score",
  "birth_date",
  "death_date",
  "given_name",
  "surname",
  "primary_name",
  "place",
  "sources",
]);

function FieldResolvers({
  preview,
  choices,
  onSide,
  onNote,
}: {
  preview: MergePreviewResponse;
  choices: Record<string, FieldChoice>;
  onSide: (field: string, side: ResolverSide) => void;
  onNote: (field: string, note: string) => void;
}) {
  const t = useTranslations("persons.merge.fields");
  const tLabel = useTranslations("persons.merge.fields.labels");
  const fields = preview.fields;

  function fieldLabel(field: string): string {
    return KNOWN_FIELD_LABEL_KEYS.has(field) ? tLabel(field as never) : field;
  }

  return (
    <section aria-labelledby="fields-heading" className="space-y-3">
      <h2 id="fields-heading" className="text-lg font-semibold">
        {t("title")}
      </h2>
      {fields.length === 0 ? (
        <p className="rounded-md bg-[color:var(--color-surface-muted)] px-3 py-2 text-sm text-[color:var(--color-ink-500)]">
          {t("noConflicts")}
        </p>
      ) : (
        <div className="space-y-3">
          {fields.map((field) => {
            const choice = choices[field.field];
            return (
              <ConflictResolver
                key={field.field}
                fieldName={field.field}
                fieldLabel={fieldLabel(field.field)}
                leftValue={field.survivor_value}
                rightValue={field.merged_value}
                selected={choice?.side ?? null}
                onChange={(side) => onSide(field.field, side)}
                note={choice?.note ?? ""}
                onNoteChange={(note) => onNote(field.field, note)}
              />
            );
          })}
        </div>
      )}

      {preview.names.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{t("namesTitle")}</CardTitle>
            <CardDescription>{t("namesBody")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm">
              {preview.names.map((n) => (
                <li key={n.name_id} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px]">{n.name_id.slice(0, 8)}…</span>
                  <span className="text-xs text-[color:var(--color-ink-500)]">
                    sort {n.old_sort_order} → {n.new_sort_order}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}

function PreviewPane({
  preview,
  primaryId,
  candidateId,
  choices,
  impliedSurvivor,
}: {
  preview: MergePreviewResponse;
  primaryId: string;
  candidateId: string;
  choices: Record<string, FieldChoice>;
  impliedSurvivor: SurvivorChoice | null;
}) {
  const t = useTranslations("persons.merge.previewPane");
  const survivorId =
    impliedSurvivor === "left"
      ? primaryId
      : impliedSurvivor === "right"
        ? candidateId
        : preview.default_survivor_id;

  return (
    <section aria-labelledby="preview-heading" className="space-y-3">
      <h2 id="preview-heading" className="text-lg font-semibold">
        {t("title")}
      </h2>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("survivor")}</CardTitle>
          <CardDescription>
            <span className="font-mono text-xs">{survivorId}</span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            {preview.fields.length === 0 ? (
              <li className="text-sm text-[color:var(--color-ink-500)]">{t("noFieldDiff")}</li>
            ) : (
              preview.fields.map((field) => {
                const choice = choices[field.field];
                const userSide = choice?.side;
                const value = pickPreviewValue(field, userSide);
                return (
                  <li key={field.field} className="text-sm">
                    <span className="font-mono text-[11px] text-[color:var(--color-ink-500)]">
                      {field.field}
                    </span>
                    <p className="mt-0.5 break-all font-mono text-[12px]">{formatValue(value)}</p>
                    {choice?.note ? (
                      <p className="mt-0.5 text-[11px] italic text-[color:var(--color-ink-500)]">
                        {t("note")}: {choice.note}
                      </p>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
        </CardContent>
      </Card>
    </section>
  );
}

function pickPreviewValue(
  field: MergePreviewResponse["fields"][number],
  side: ResolverSide | null | undefined,
): unknown {
  if (side === "left") return field.survivor_value;
  if (side === "right") return field.merged_value;
  return field.after_merge_value;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function ConflictBlock({ conflicts }: { conflicts: MergePreviewResponse["conflicts"] }) {
  const t = useTranslations("persons.merge.conflicts");
  if (conflicts.length === 0) {
    return (
      <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900 ring-1 ring-emerald-200">
        {t("none")}
      </p>
    );
  }
  return (
    <div className="rounded-md bg-amber-50 px-3 py-2 ring-1 ring-amber-200">
      <p className="text-sm font-medium text-amber-900">
        {t("blockedTitle", { count: conflicts.length })}
      </p>
      <ul className="mt-2 space-y-1 text-sm text-amber-900">
        {conflicts.map((c) => (
          <li key={`${c.reason}-${c.hypothesis_id ?? "no-hyp"}-${c.detail}`}>
            <Badge variant="outline">{c.reason}</Badge> {c.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConfirmDialog({
  blocked,
  reviewed,
  committing,
  commitError,
  onReviewedChange,
  onConfirm,
  primaryId,
  candidateId,
  impliedSurvivor,
}: {
  blocked: boolean;
  reviewed: boolean;
  committing: boolean;
  commitError: unknown;
  onReviewedChange: (next: boolean) => void;
  onConfirm: () => void;
  primaryId: string;
  candidateId: string;
  impliedSurvivor: SurvivorChoice | null;
}) {
  const t = useTranslations("persons.merge.confirm");
  const errorMessage = formatCommitError(commitError, t);
  const survivorLabel =
    impliedSurvivor === "left"
      ? t("survivorPrimary")
      : impliedSurvivor === "right"
        ? t("survivorCandidate")
        : t("survivorDefault");

  return (
    <section aria-labelledby="confirm-heading" className="space-y-3">
      <h2 id="confirm-heading" className="text-lg font-semibold">
        {t("title")}
      </h2>
      <p className="text-sm text-[color:var(--color-ink-500)]">
        {t("body", {
          primary: primaryId.slice(0, 8),
          candidate: candidateId.slice(0, 8),
          survivor: survivorLabel,
        })}
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={reviewed}
          onChange={(event) => onReviewedChange(event.target.checked)}
          className="h-4 w-4 accent-[color:var(--color-accent)]"
        />
        <span>{t("reviewedCheckbox")}</span>
      </label>
      <Button
        variant="primary"
        size="md"
        onClick={onConfirm}
        disabled={blocked || !reviewed || committing}
      >
        {committing ? t("submitting") : t("submit")}
      </Button>
      {errorMessage ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-900 ring-1 ring-red-200">
          {errorMessage}
        </p>
      ) : null}
    </section>
  );
}

function MergeSuccess({
  result,
  onBack,
}: {
  result: MergeCommitResponse;
  onBack: () => void;
}) {
  const t = useTranslations("persons.merge.success");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>
          {t("body", {
            survivor: result.survivor_id.slice(0, 8),
            merged: result.merged_id.slice(0, 8),
          })}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-[color:var(--color-ink-500)]">
          merge_id <span className="font-mono">{result.merge_id}</span> · merged_at{" "}
          {result.merged_at}
        </p>
      </CardContent>
      <CardContent className="flex flex-wrap gap-2">
        <Button variant="primary" size="sm" onClick={onBack}>
          {t("openSurvivor")}
        </Button>
        <Button variant="secondary" size="sm" asChild>
          <Link href={`/persons/${result.survivor_id}/merge-log`}>{t("viewLog")}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function MergeLoadingSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-5 w-1/3" />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

function MergeErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const t = useTranslations("persons.merge.errorState");
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : t("unknown");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{message}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="primary" size="sm" onClick={onRetry}>
          {t("retry")}
        </Button>
      </CardContent>
    </Card>
  );
}

function MissingCandidateState({ primaryId }: { primaryId: string }) {
  const t = useTranslations("persons.merge.missingCandidate");
  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("body")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="primary" size="sm" asChild>
            <Link href={`/persons/${primaryId}`}>{t("backToPerson")}</Link>
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}

function formatCommitError(
  error: unknown,
  t: ReturnType<typeof useTranslations<"persons.merge.confirm">>,
): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 409) return t("error409");
    if (error.status === 422) return t("error422");
    return `${error.status}: ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return t("errorUnknown");
}
