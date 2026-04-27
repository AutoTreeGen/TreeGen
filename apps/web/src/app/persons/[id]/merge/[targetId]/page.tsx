"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

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

// CLAUDE.md §5: auto-merge запрещён. Confirm dialog требует явного
// чекбокса перед commit'ом, токен генерируется per-mount для idempotency.
function makeConfirmToken(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function MergePage() {
  const router = useRouter();
  const params = useParams<{ id: string; targetId: string }>();
  const personId = params.id;
  const targetId = params.targetId;

  const [survivorChoice, setSurvivorChoice] = useState<SurvivorChoice | null>(null);
  const [reviewed, setReviewed] = useState(false);
  // confirm_token живёт в state — один токен на одну mount-сессию страницы;
  // если пользователь refresh'ит — получит новый, и idempotency-проверка
  // в backend'е не сработает (но это OK: refresh после успешного merge
  // покажет 409 subject_already_merged, что мы корректно обрабатываем).
  const [confirmToken] = useState(makeConfirmToken);
  const [committed, setCommitted] = useState<MergeCommitResponse | null>(null);

  const previewQuery = useQuery({
    queryKey: ["merge-preview", personId, targetId, survivorChoice],
    queryFn: () =>
      fetchMergePreview(personId, {
        target_id: targetId,
        survivor_choice: survivorChoice,
        confirm_token: confirmToken,
      }),
    enabled: Boolean(personId && targetId) && committed === null,
  });

  const commitMutation = useMutation({
    mutationFn: () =>
      commitMerge(personId, {
        target_id: targetId,
        confirm: true,
        confirm_token: confirmToken,
        survivor_choice: survivorChoice,
      }),
    onSuccess: (data) => {
      setCommitted(data);
    },
  });

  const preview = previewQuery.data;
  const blocked = preview?.conflicts && preview.conflicts.length > 0;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/persons/${personId}`}>← Back to person</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Merge two persons</h1>
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--color-ink-500)]">
          Manual review only — CLAUDE.md §5 forbids auto-merge for close kin. The system will{" "}
          <strong>soft-delete</strong> the merged person and give you 90 days to undo from{" "}
          <code>merge-history</code>.
        </p>
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
          <SurvivorPicker
            personId={personId}
            targetId={targetId}
            preview={preview}
            survivorChoice={survivorChoice}
            onChange={setSurvivorChoice}
          />

          <Separator className="my-6" />

          <DiffSections preview={preview} />

          <Separator className="my-6" />

          <ConflictBlock conflicts={preview.conflicts} />

          <Separator className="my-6" />

          <ConfirmDialog
            blocked={Boolean(blocked)}
            reviewed={reviewed}
            committing={commitMutation.isPending}
            commitError={commitMutation.error}
            onReviewedChange={setReviewed}
            onConfirm={() => commitMutation.mutate()}
            survivorId={preview.survivor_id}
            mergedId={preview.merged_id}
          />
        </>
      ) : null}
    </main>
  );
}

function SurvivorPicker({
  personId,
  targetId,
  preview,
  survivorChoice,
  onChange,
}: {
  personId: string;
  targetId: string;
  preview: MergePreviewResponse;
  survivorChoice: SurvivorChoice | null;
  onChange: (next: SurvivorChoice) => void;
}) {
  // "left" = path-personId, "right" = targetId. После переключения toggle
  // backend пересчитывает diff с другим survivor'ом.
  const effectiveLeftIsSurvivor = preview.survivor_id === personId;
  const isLeftActive =
    survivorChoice === "left" || (survivorChoice === null && effectiveLeftIsSurvivor);
  const isRightActive =
    survivorChoice === "right" || (survivorChoice === null && !effectiveLeftIsSurvivor);

  return (
    <section aria-labelledby="survivor-heading">
      <h2 id="survivor-heading" className="mb-3 text-lg font-semibold">
        Choose survivor
      </h2>
      <p className="mb-4 text-sm text-[color:var(--color-ink-500)]">
        The survivor keeps the canonical record; the other becomes soft-deleted and redirects to the
        survivor. Default picks the entity with more provenance evidence.
      </p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <SurvivorChoiceCard
          title="Keep left"
          subtitle={personId}
          active={isLeftActive}
          isDefault={effectiveLeftIsSurvivor}
          onSelect={() => onChange("left")}
        />
        <SurvivorChoiceCard
          title="Keep right"
          subtitle={targetId}
          active={isRightActive}
          isDefault={!effectiveLeftIsSurvivor}
          onSelect={() => onChange("right")}
        />
      </div>
    </section>
  );
}

function SurvivorChoiceCard({
  title,
  subtitle,
  active,
  isDefault,
  onSelect,
}: {
  title: string;
  subtitle: string;
  active: boolean;
  isDefault: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={cn(
        "rounded-lg border p-4 text-left transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2",
        active
          ? "border-[color:var(--color-accent)] bg-[color:var(--color-surface)] ring-1 ring-[color:var(--color-accent)]"
          : "border-[color:var(--color-border)] bg-[color:var(--color-surface)] hover:bg-[color:var(--color-surface-muted)]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{title}</span>
        {isDefault ? <Badge variant="neutral">default</Badge> : null}
      </div>
      <p className="mt-1 break-all font-mono text-xs text-[color:var(--color-ink-500)]">
        {subtitle}
      </p>
    </button>
  );
}

function DiffSections({ preview }: { preview: MergePreviewResponse }) {
  const { fields, names, events, family_memberships } = preview;

  return (
    <section aria-labelledby="diff-heading">
      <h2 id="diff-heading" className="mb-3 text-lg font-semibold">
        What will change
      </h2>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <DiffSummaryCard title="Person fields">
          {fields.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No field changes.</p>
          ) : (
            <ul className="space-y-2">
              {fields.map((f) => (
                <li key={f.field} className="text-sm">
                  <span className="font-mono text-xs text-[color:var(--color-ink-500)]">
                    {f.field}
                  </span>
                  <div className="mt-1 grid grid-cols-3 items-baseline gap-2 text-xs">
                    <DiffValue label="A" value={f.survivor_value} />
                    <DiffValue label="B" value={f.merged_value} />
                    <DiffValue label="→" value={f.after_merge_value} highlight />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </DiffSummaryCard>

        <DiffSummaryCard title="Names">
          {names.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No alternative names move.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {names.map((n) => (
                <li key={n.name_id} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px]">{n.name_id.slice(0, 8)}…</span>
                  <span className="text-xs text-[color:var(--color-ink-500)]">
                    sort {n.old_sort_order} → {n.new_sort_order}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </DiffSummaryCard>

        <DiffSummaryCard title="Events">
          {events.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No event changes.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {events.map((e) => (
                <li key={e.event_id} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px]">{e.event_id.slice(0, 8)}…</span>
                  <Badge variant={e.action === "collapse_into_survivor" ? "accent" : "outline"}>
                    {e.action.replaceAll("_", " ")}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </DiffSummaryCard>

        <DiffSummaryCard title="Family memberships">
          {family_memberships.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No family-FK reparents.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {family_memberships.map((fm) => (
                <li key={`${fm.table}-${fm.row_id}`} className="flex items-center gap-2">
                  <Badge variant="neutral">{fm.table}</Badge>
                  <span className="truncate font-mono text-[11px]">{fm.row_id.slice(0, 8)}…</span>
                </li>
              ))}
            </ul>
          )}
        </DiffSummaryCard>
      </div>
    </section>
  );
}

function DiffSummaryCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function DiffValue({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: unknown;
  highlight?: boolean;
}) {
  const display = value === null || value === undefined ? "—" : String(value);
  return (
    <div
      className={cn(
        "rounded px-2 py-1",
        highlight
          ? "bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300"
          : "bg-[color:var(--color-surface-muted)] text-[color:var(--color-ink-700)]",
      )}
    >
      <p className="text-[10px] font-medium uppercase tracking-wide opacity-60">{label}</p>
      <p className="break-all font-mono text-[11px]">{display}</p>
    </div>
  );
}

function ConflictBlock({ conflicts }: { conflicts: MergePreviewResponse["conflicts"] }) {
  if (conflicts.length === 0) {
    return (
      <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900 ring-1 ring-emerald-200">
        No blocking hypothesis conflicts. Merge can proceed after manual review.
      </p>
    );
  }
  return (
    <div className="rounded-md bg-amber-50 px-3 py-2 ring-1 ring-amber-200">
      <p className="text-sm font-medium text-amber-900">
        Merge blocked by {conflicts.length} conflict
        {conflicts.length === 1 ? "" : "s"}:
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
  survivorId,
  mergedId,
}: {
  blocked: boolean;
  reviewed: boolean;
  committing: boolean;
  commitError: unknown;
  onReviewedChange: (next: boolean) => void;
  onConfirm: () => void;
  survivorId: string;
  mergedId: string;
}) {
  const errorMessage = formatCommitError(commitError);
  return (
    <section aria-labelledby="confirm-heading" className="space-y-3">
      <h2 id="confirm-heading" className="text-lg font-semibold">
        Confirm merge
      </h2>
      <p className="text-sm text-[color:var(--color-ink-500)]">
        This will merge <span className="font-mono text-xs">{mergedId.slice(0, 8)}…</span> into{" "}
        <span className="font-mono text-xs">{survivorId.slice(0, 8)}…</span> (soft-delete with
        redirect). You have <strong>90 days</strong> to undo from the survivor&apos;s merge-history.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={reviewed}
          onChange={(event) => onReviewedChange(event.target.checked)}
          className="h-4 w-4 accent-[color:var(--color-accent)]"
        />
        <span>I reviewed the diff above and confirm this is correct.</span>
      </label>
      <Button
        variant="primary"
        size="md"
        onClick={onConfirm}
        disabled={blocked || !reviewed || committing}
      >
        {committing ? "Merging…" : "Confirm merge"}
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
  return (
    <Card>
      <CardHeader>
        <CardTitle>Merge committed</CardTitle>
        <CardDescription>
          Survivor: <span className="font-mono text-xs">{result.survivor_id}</span>. Merged:{" "}
          <span className="font-mono text-xs">{result.merged_id}</span>. Undo within 90 days from
          the survivor&apos;s merge-history.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-[color:var(--color-ink-500)]">
          merge_id <span className="font-mono">{result.merge_id}</span> · merged_at{" "}
          {result.merged_at}
        </p>
      </CardContent>
      <CardContent>
        <Button variant="primary" size="sm" onClick={onBack}>
          Open survivor
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
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load merge preview</CardTitle>
        <CardDescription>{message}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="primary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}

function formatCommitError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "Merge blocked by hypothesis conflicts (HTTP 409). Review the conflict block above.";
    }
    if (error.status === 422) {
      return "Server rejected the request (HTTP 422). Refresh and try again.";
    }
    return `${error.status}: ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
