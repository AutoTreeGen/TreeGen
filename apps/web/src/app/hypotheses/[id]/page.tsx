"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type HypothesisEvidence,
  type HypothesisResponse,
  type HypothesisReviewStatus,
  type HypothesisType,
  fetchHypothesis,
  reviewHypothesis,
} from "@/lib/api";

type ReviewAction = "confirmed" | "rejected" | "deferred";

export default function HypothesisDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const hypothesisId = params.id;

  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);
  const [reasonDraft, setReasonDraft] = useState("");

  const query = useQuery({
    queryKey: ["hypothesis", hypothesisId],
    queryFn: () => fetchHypothesis(hypothesisId),
    enabled: Boolean(hypothesisId),
  });

  const reviewMutation = useMutation({
    mutationFn: ({ status, note }: { status: HypothesisReviewStatus; note?: string }) =>
      reviewHypothesis(hypothesisId, { status, note: note || null }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["hypothesis", hypothesisId], updated);
      // Если same_person + approved — после mutation редиректим на merge UI.
      if (
        pendingAction === "confirmed" &&
        updated.hypothesis_type === "same_person" &&
        updated.subject_a_type === "person" &&
        updated.subject_b_type === "person"
      ) {
        const target = `/persons/${updated.subject_a_id}/merge/${updated.subject_b_id}?from_hypothesis=${updated.id}`;
        router.push(target);
        return;
      }
      setPendingAction(null);
      setReasonDraft("");
    },
  });

  const data = query.data;
  const isReviewed = data && data.reviewed_status !== "pending";

  const submitReview = (action: ReviewAction) => {
    setPendingAction(action);
    reviewMutation.mutate({ status: action, note: reasonDraft || undefined });
  };

  return (
    <main className="mx-auto max-w-4xl px-6 py-10 pb-32">
      <header className="mb-8 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
            Hypothesis
          </p>
          <h1 className="font-mono text-sm text-[color:var(--color-ink-700)]">{hypothesisId}</h1>
          {data ? (
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">
              {humanPredicate(data.hypothesis_type)}
            </h2>
          ) : null}
        </div>
        <Button variant="ghost" size="md" asChild>
          <Link href={data ? `/trees/${data.tree_id}/hypotheses` : "/"}>← Back to queue</Link>
        </Button>
      </header>

      {query.isLoading ? <DetailSkeleton /> : null}

      {query.isError ? (
        <Card className="border-red-200 ring-red-200">
          <CardHeader>
            <CardTitle>Couldn&apos;t load hypothesis</CardTitle>
            <CardDescription>
              {query.error instanceof ApiError
                ? `${query.error.status}: ${query.error.message}`
                : "Unknown error"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="primary" size="sm" onClick={() => query.refetch()}>
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {data ? (
        <>
          <SubjectsSection hypothesis={data} />
          <ScoreSection hypothesis={data} />
          <EvidenceSection evidences={data.evidences} />
          <HistorySection hypothesis={data} />
          {!isReviewed ? (
            <ReasonField
              value={reasonDraft}
              onChange={setReasonDraft}
              disabled={reviewMutation.isPending}
            />
          ) : null}
          <ActionsRow
            hypothesis={data}
            isReviewed={Boolean(isReviewed)}
            isPending={reviewMutation.isPending}
            errorMessage={
              reviewMutation.error
                ? reviewMutation.error instanceof ApiError
                  ? `${reviewMutation.error.status}: ${reviewMutation.error.message}`
                  : "Review failed."
                : null
            }
            onAction={submitReview}
          />
        </>
      ) : null}
    </main>
  );
}

/* ---------- Sections ---------------------------------------------------- */

function SubjectsSection({ hypothesis }: { hypothesis: HypothesisResponse }) {
  return (
    <section aria-label="Subjects" className="mb-6 grid grid-cols-1 gap-3 md:grid-cols-2">
      <SubjectCard
        label="Subject A"
        type={hypothesis.subject_a_type}
        id={hypothesis.subject_a_id}
      />
      <SubjectCard
        label="Subject B"
        type={hypothesis.subject_b_type}
        id={hypothesis.subject_b_id}
      />
    </section>
  );
}

function SubjectCard({ label, type, id }: { label: string; type: string; id: string }) {
  // Только для person-сущностей у нас есть detail-страница (`/persons/[id]`).
  // Source / place — Phase 4.7+ (link отключаем для них).
  const linkable = type === "person";
  return (
    <Card>
      <CardHeader>
        <CardDescription>
          {label} <Badge variant="outline">{type}</Badge>
        </CardDescription>
        <CardTitle className="font-mono text-sm break-all">{id}</CardTitle>
      </CardHeader>
      {linkable ? (
        <CardContent>
          <Button variant="link" size="sm" asChild>
            <Link href={`/persons/${id}`}>Open profile →</Link>
          </Button>
        </CardContent>
      ) : null}
    </Card>
  );
}

function ScoreSection({ hypothesis }: { hypothesis: HypothesisResponse }) {
  const t = useTranslations("hypotheses.detail");
  const pct = Math.round(hypothesis.composite_score * 100);
  const color =
    hypothesis.composite_score >= 0.7
      ? "bg-emerald-500"
      : hypothesis.composite_score >= 0.4
        ? "bg-amber-500"
        : "bg-red-500";
  return (
    <section aria-label="Score" className="mb-6">
      <Card>
        <CardHeader>
          <CardDescription>{t("compositeConfidence")}</CardDescription>
          <CardTitle className="text-3xl font-bold">
            {hypothesis.composite_score.toFixed(2)}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <div
              role="meter"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Confidence ${pct}%`}
              className="h-3 flex-1 overflow-hidden rounded-full bg-[color:var(--color-surface-muted)]"
            >
              <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="w-14 text-right font-mono text-sm">{pct}%</span>
          </div>
          <p className="mt-2 text-xs text-[color:var(--color-ink-500)]">
            Computed at {new Date(hypothesis.computed_at).toLocaleString()} · rules{" "}
            <span className="font-mono">{hypothesis.rules_version}</span>
          </p>
        </CardContent>
      </Card>
    </section>
  );
}

function EvidenceSection({ evidences }: { evidences: HypothesisEvidence[] }) {
  const t = useTranslations("hypotheses.detail");
  // Группируем по rule_id — внутри одной "карты evidence" могут быть SUPPORTS
  // и CONTRADICTS от одного rule (например, surname rule выдаёт оба для разных
  // имён персоны). Группировка делает breakdown компактнее.
  const grouped = useMemo(() => {
    const map = new Map<string, HypothesisEvidence[]>();
    for (const ev of evidences) {
      const list = map.get(ev.rule_id) ?? [];
      list.push(ev);
      map.set(ev.rule_id, list);
    }
    return [...map.entries()];
  }, [evidences]);

  if (evidences.length === 0) {
    return (
      <section aria-label="Evidence" className="mb-6">
        <Card>
          <CardHeader>
            <CardTitle>{t("noEvidence")}</CardTitle>
            <CardDescription>
              This hypothesis was persisted without supporting evidences. Likely the rule version
              changed since it was generated; consider re-running compute.
            </CardDescription>
          </CardHeader>
        </Card>
      </section>
    );
  }

  return (
    <section aria-label="Evidence" className="mb-6">
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-[color:var(--color-ink-500)]">
        Evidence breakdown ({evidences.length})
      </h3>
      <ul className="flex flex-col gap-2">
        {grouped.map(([ruleId, items]) => (
          <li key={ruleId}>
            <EvidenceRuleCard ruleId={ruleId} items={items} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceRuleCard({ ruleId, items }: { ruleId: string; items: HypothesisEvidence[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          <code className="rounded bg-[color:var(--color-surface-muted)] px-1.5 py-0.5 font-mono text-xs">
            {ruleId}
          </code>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-2">
          {items.map((ev) => (
            <li key={ev.id} className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-sm">
                <DirectionBadge direction={ev.direction} weight={ev.weight} />
                <span className="text-[color:var(--color-ink-700)]">{ev.observation}</span>
              </div>
              {ev.rule_id === "dna_segment" || ev.rule_id.startsWith("dna_") ? (
                <DnaSegmentBlock provenance={ev.source_provenance} />
              ) : null}
              {ev.rule_id.startsWith("source_") ? (
                <SourceCitationBlock provenance={ev.source_provenance} />
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function DirectionBadge({
  direction,
  weight,
}: {
  direction: HypothesisEvidence["direction"];
  weight: number;
}) {
  const sign = direction === "supports" ? "+" : direction === "contradicts" ? "−" : "·";
  const variant =
    direction === "supports" ? "accent" : direction === "contradicts" ? "outline" : "neutral";
  return (
    <Badge variant={variant}>
      {sign} {weight.toFixed(2)}
    </Badge>
  );
}

/**
 * Phase 7.3+ DNA segment evidence имеет ``source_provenance.segments`` —
 * массив ``{chromosome, start_bp, end_bp, cm}``. Phase 4.9 показывает их
 * как list (chromosome bar — Phase 4.9.1, MVP остаётся текстом).
 *
 * Privacy: НЕ показываем raw rsids / genotypes (CLAUDE.md §3.5, ADR-0012).
 * Только агрегаты: chromosome, start/end positions, cM.
 */
function DnaSegmentBlock({ provenance }: { provenance: Record<string, unknown> }) {
  const segments = (provenance.segments as DnaSegment[] | undefined) ?? [];
  if (segments.length === 0) return null;
  const totalCm = segments.reduce((sum, s) => sum + (Number(s.cm) || 0), 0);
  const endogamy = Boolean(provenance.endogamy_adjusted);
  return (
    <div className="ml-7 rounded-md bg-[color:var(--color-surface-muted)] p-3 text-xs">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="font-semibold">DNA segments ({segments.length})</span>
        <span className="text-[color:var(--color-ink-500)]">total {totalCm.toFixed(1)} cM</span>
        {endogamy ? <Badge variant="outline">AJ-adjusted</Badge> : null}
      </div>
      <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        {segments.map((seg, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: stable order, no reorder
          <li key={i} className="font-mono text-[11px] text-[color:var(--color-ink-700)]">
            chr{seg.chromosome}: {seg.start_bp}–{seg.end_bp} ·{" "}
            {seg.cm !== undefined ? `${Number(seg.cm).toFixed(1)} cM` : "?"}
          </li>
        ))}
      </ul>
    </div>
  );
}

type DnaSegment = {
  chromosome?: string | number;
  start_bp?: number;
  end_bp?: number;
  cm?: number | string;
};

function SourceCitationBlock({ provenance }: { provenance: Record<string, unknown> }) {
  const sourceTitle = (provenance.source_title as string | undefined) ?? null;
  const sourceId = (provenance.source_id as string | undefined) ?? null;
  const page = (provenance.page as string | undefined) ?? null;
  const quay = (provenance.quay as number | string | undefined) ?? null;
  if (!sourceTitle && !sourceId) return null;
  return (
    <div className="ml-7 rounded-md bg-[color:var(--color-surface-muted)] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Source:</span>
        {sourceId ? (
          <Link
            href={`/sources/${sourceId}`}
            className="text-[color:var(--color-accent)] underline-offset-4 hover:underline"
          >
            {sourceTitle ?? sourceId}
          </Link>
        ) : (
          <span>{sourceTitle}</span>
        )}
        {page ? <span className="text-[color:var(--color-ink-500)]">p. {page}</span> : null}
        {quay !== null ? <Badge variant="outline">QUAY {quay}</Badge> : null}
      </div>
    </div>
  );
}

function HistorySection({ hypothesis }: { hypothesis: HypothesisResponse }) {
  if (hypothesis.reviewed_status === "pending" || hypothesis.reviewed_at === null) return null;
  const verb =
    hypothesis.reviewed_status === "confirmed"
      ? "Approved"
      : hypothesis.reviewed_status === "rejected"
        ? "Rejected"
        : "Deferred";
  return (
    <section aria-label="Review history" className="mb-6">
      <Card>
        <CardHeader>
          <CardDescription>Review history</CardDescription>
          <CardTitle className="text-base">
            {verb} on {new Date(hypothesis.reviewed_at).toLocaleString()}
            {hypothesis.reviewed_by_user_id ? (
              <span className="ml-1 font-mono text-xs text-[color:var(--color-ink-500)]">
                by {hypothesis.reviewed_by_user_id.slice(0, 8)}
              </span>
            ) : null}
          </CardTitle>
        </CardHeader>
        {hypothesis.review_note ? (
          <CardContent>
            <p className="text-sm whitespace-pre-line">{hypothesis.review_note}</p>
          </CardContent>
        ) : null}
      </Card>
    </section>
  );
}

function ReasonField({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
}) {
  return (
    <section aria-label="Optional reviewer note" className="mb-4">
      <label
        htmlFor="hyp-review-reason"
        className="mb-1 block text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
      >
        Reviewer note (optional)
      </label>
      <textarea
        id="hyp-review-reason"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={2}
        maxLength={2000}
        placeholder="Why? (e.g. 'Same person — birth date confirmed by archive scan #1234')"
        className="w-full rounded-md bg-[color:var(--color-surface)] px-3 py-2 text-sm ring-1 ring-[color:var(--color-border)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
      />
    </section>
  );
}

/**
 * Sticky bottom action row. Approve по `same_person` редиректит в Phase 4.6
 * merge UI после сохранения review (см. mutation.onSuccess в parent).
 */
function ActionsRow({
  hypothesis,
  isReviewed,
  isPending,
  errorMessage,
  onAction,
}: {
  hypothesis: HypothesisResponse;
  isReviewed: boolean;
  isPending: boolean;
  errorMessage: string | null;
  onAction: (action: ReviewAction) => void;
}) {
  const isSamePerson =
    hypothesis.hypothesis_type === "same_person" &&
    hypothesis.subject_a_type === "person" &&
    hypothesis.subject_b_type === "person";

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 shadow-[0_-2px_12px_rgba(0,0,0,0.05)]">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3">
        {errorMessage ? (
          <p className="text-sm text-red-600">{errorMessage}</p>
        ) : (
          <p className="text-xs text-[color:var(--color-ink-500)]">
            {isReviewed
              ? "Already reviewed — actions are disabled. Re-open from list to review again."
              : isSamePerson
                ? "Approve will save your judgment, then open the manual merge UI."
                : "Approve / Reject saves your judgment. Domain entities are not auto-merged."}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="md"
            disabled={isReviewed || isPending}
            onClick={() => onAction("deferred")}
          >
            Defer
          </Button>
          <Button
            variant="secondary"
            size="md"
            disabled={isReviewed || isPending}
            onClick={() => onAction("rejected")}
            className="text-red-700"
          >
            Reject
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={isReviewed || isPending}
            onClick={() => onAction("confirmed")}
            className="bg-emerald-600 hover:bg-emerald-700"
          >
            {isSamePerson ? "Approve & merge" : "Approve"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

function humanPredicate(type: HypothesisType): string {
  switch (type) {
    case "same_person":
      return "Same-person hypothesis";
    case "parent_child":
      return "Parent–child relationship";
    case "siblings":
      return "Siblings hypothesis";
    case "marriage":
      return "Marriage hypothesis";
    case "duplicate_source":
      return "Duplicate source hypothesis";
    case "duplicate_place":
      return "Duplicate place hypothesis";
  }
}
