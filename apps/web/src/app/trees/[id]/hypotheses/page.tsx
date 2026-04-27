"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type HypothesisReviewStatus,
  type HypothesisSummary,
  type HypothesisType,
  fetchHypotheses,
} from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 250;

const STATUS_OPTIONS: { value: HypothesisReviewStatus | "all"; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "confirmed", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "deferred", label: "Deferred" },
  { value: "all", label: "All" },
];

const TYPE_OPTIONS: { value: HypothesisType | "all"; label: string }[] = [
  { value: "all", label: "All types" },
  { value: "same_person", label: "Same person" },
  { value: "parent_child", label: "Parent–child" },
  { value: "siblings", label: "Siblings" },
  { value: "marriage", label: "Marriage" },
  { value: "duplicate_source", label: "Duplicate source" },
  { value: "duplicate_place", label: "Duplicate place" },
];

/**
 * Привести raw search-param к валидному ``HypothesisReviewStatus | "all"``.
 * Дефолт — ``"pending"`` (то, ради чего пользователь сюда пришёл).
 */
function parseStatus(raw: string | null): HypothesisReviewStatus | "all" {
  if (raw && STATUS_OPTIONS.some((o) => o.value === raw)) {
    return raw as HypothesisReviewStatus | "all";
  }
  return "pending";
}

function parseType(raw: string | null): HypothesisType | "all" {
  if (raw && TYPE_OPTIONS.some((o) => o.value === raw)) {
    return raw as HypothesisType | "all";
  }
  return "all";
}

function parseConfidence(raw: string | null): number {
  if (!raw) return 0.5;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 1 ? n : 0.5;
}

export default function HypothesesListPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const treeId = params.id;

  const [status, setStatus] = useState<HypothesisReviewStatus | "all">(
    parseStatus(searchParams.get("status")),
  );
  const [hypType, setHypType] = useState<HypothesisType | "all">(
    parseType(searchParams.get("type")),
  );
  const [confidenceInput, setConfidenceInput] = useState<string>(
    searchParams.get("min_confidence") ?? "0.5",
  );
  const offset = Number(searchParams.get("offset") ?? "0") || 0;

  const debouncedConfidenceRaw = useDebouncedValue(confidenceInput, SEARCH_DEBOUNCE_MS);
  const minConfidence = useMemo(
    () => parseConfidence(debouncedConfidenceRaw),
    [debouncedConfidenceRaw],
  );

  // Sync URL state. router.replace, без scroll-jump.
  useEffect(() => {
    if (!treeId) return;
    const sp = new URLSearchParams();
    if (status !== "pending") sp.set("status", status);
    if (hypType !== "all") sp.set("type", hypType);
    if (Math.abs(minConfidence - 0.5) > 1e-6) sp.set("min_confidence", String(minConfidence));
    if (offset > 0) sp.set("offset", String(offset));
    const target = sp.toString();
    if (target !== searchParams.toString()) {
      router.replace(`/trees/${treeId}/hypotheses${target ? `?${target}` : ""}`, {
        scroll: false,
      });
    }
  }, [status, hypType, minConfidence, offset, router, searchParams, treeId]);

  const query = useQuery({
    queryKey: ["hypotheses", treeId, { status, hypType, minConfidence, offset }],
    queryFn: () =>
      fetchHypotheses(treeId, {
        reviewStatus: status === "all" ? null : status,
        hypothesisType: hypType === "all" ? null : hypType,
        minConfidence,
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: Boolean(treeId),
  });

  const data = query.data;
  const total = data?.total ?? 0;
  const lastPageOffset = total > 0 ? Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE : 0;
  const canPrev = offset > 0;
  const canNext = total > 0 && offset + PAGE_SIZE < total;

  const setOffset = (next: number) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (next <= 0) sp.delete("offset");
    else sp.set("offset", String(next));
    const qs = sp.toString();
    router.push(`/trees/${treeId}/hypotheses${qs ? `?${qs}` : ""}`);
  };

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">Tree</p>
          <h1 className="font-mono text-sm text-[color:var(--color-ink-700)]">{treeId}</h1>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">Hypotheses</h2>
          {data ? (
            <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
              {total.toLocaleString("en-US")} {total === 1 ? "hypothesis" : "hypotheses"}
              {total > 0 ? (
                <>
                  {" "}
                  · showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)}
                </>
              ) : null}
            </p>
          ) : null}
        </div>
        <Button variant="ghost" size="md" asChild>
          <Link href={`/trees/${treeId}/persons`}>← Back to persons</Link>
        </Button>
      </header>

      <section
        aria-label="Hypothesis filters"
        className="mb-6 flex flex-col gap-3 rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 sm:flex-row sm:items-end"
      >
        <div className="flex flex-1 flex-col gap-1.5">
          <label
            htmlFor="hyp-filter-status"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Status
          </label>
          <select
            id="hyp-filter-status"
            value={status}
            onChange={(e) => setStatus(e.target.value as HypothesisReviewStatus | "all")}
            className="h-10 rounded-md bg-[color:var(--color-surface)] px-3 text-sm ring-1 ring-[color:var(--color-border)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-1 flex-col gap-1.5">
          <label
            htmlFor="hyp-filter-type"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Type
          </label>
          <select
            id="hyp-filter-type"
            value={hypType}
            onChange={(e) => setHypType(e.target.value as HypothesisType | "all")}
            className="h-10 rounded-md bg-[color:var(--color-surface)] px-3 text-sm ring-1 ring-[color:var(--color-border)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex w-44 flex-col gap-1.5">
          <label
            htmlFor="hyp-filter-confidence"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Min confidence
          </label>
          <Input
            id="hyp-filter-confidence"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={confidenceInput}
            onChange={(e) => setConfidenceInput(e.target.value)}
          />
        </div>
      </section>

      {query.isLoading ? <HypothesesSkeleton /> : null}

      {query.isError ? (
        <HypothesesError error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {data && data.items.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No matching hypotheses</CardTitle>
            <CardDescription>
              {status === "pending"
                ? "Nothing pending right now. Inference rules generate hypotheses on import; check back after the next compute run."
                : `No ${status} hypotheses match these filters. Try widening the type / confidence filters.`}
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {data && data.items.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {data.items.map((hyp) => (
            <HypothesisRow key={hyp.id} hypothesis={hyp} />
          ))}
        </ul>
      ) : null}

      {data && data.items.length > 0 ? (
        <nav className="mt-8 flex items-center justify-between gap-3" aria-label="Pagination">
          <Button
            variant="secondary"
            size="md"
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={!canPrev}
          >
            Previous
          </Button>
          <span className="text-sm text-[color:var(--color-ink-500)]">
            Page {Math.floor(offset / PAGE_SIZE) + 1} of{" "}
            {Math.floor(lastPageOffset / PAGE_SIZE) + 1}
          </span>
          <Button
            variant="secondary"
            size="md"
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={!canNext}
          >
            Next
          </Button>
        </nav>
      ) : null}
    </main>
  );
}

/** Один ряд гипотезы в списке: confidence-bar + type/status badges + Review button. */
function HypothesisRow({ hypothesis }: { hypothesis: HypothesisSummary }) {
  const score = hypothesis.composite_score;
  const pct = Math.round(score * 100);
  // Color buckets: red <0.4, yellow 0.4–0.7, green 0.7+. Соответствует ROADMAP §7.4
  // calibration buckets, но без формальной calibration table — это MVP-визуал.
  const color = score >= 0.7 ? "bg-emerald-500" : score >= 0.4 ? "bg-amber-500" : "bg-red-500";

  return (
    <li className="rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-1 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="outline">{prettyType(hypothesis.hypothesis_type)}</Badge>
            <ReviewStatusBadge status={hypothesis.reviewed_status} />
            <span className="font-mono text-xs text-[color:var(--color-ink-500)]">
              {hypothesis.subject_a_id.slice(0, 8)} ↔ {hypothesis.subject_b_id.slice(0, 8)}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div
              role="meter"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Confidence ${pct}%`}
              className="h-2 flex-1 overflow-hidden rounded-full bg-[color:var(--color-surface-muted)]"
            >
              <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="w-14 text-right font-mono text-xs text-[color:var(--color-ink-700)]">
              {score.toFixed(2)}
            </span>
          </div>
        </div>
        <Button variant="primary" size="sm" asChild>
          <Link href={`/hypotheses/${hypothesis.id}`}>Review →</Link>
        </Button>
      </div>
    </li>
  );
}

function ReviewStatusBadge({ status }: { status: HypothesisReviewStatus }) {
  const variant: "neutral" | "outline" | "accent" =
    status === "confirmed" ? "accent" : status === "rejected" ? "outline" : "neutral";
  const label =
    status === "confirmed"
      ? "Approved"
      : status === "rejected"
        ? "Rejected"
        : status === "deferred"
          ? "Deferred"
          : "Pending";
  return <Badge variant={variant}>{label}</Badge>;
}

function prettyType(type: HypothesisType): string {
  switch (type) {
    case "same_person":
      return "Same person";
    case "parent_child":
      return "Parent–child";
    case "siblings":
      return "Siblings";
    case "marriage":
      return "Marriage";
    case "duplicate_source":
      return "Duplicate source";
    case "duplicate_place":
      return "Duplicate place";
  }
}

function HypothesesSkeleton() {
  return (
    <ul className="flex flex-col gap-2">
      {Array.from({ length: 8 }).map((_, idx) => (
        <li
          // biome-ignore lint/suspicious/noArrayIndexKey: статичный список без перестроек
          key={idx}
          className="rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4"
        >
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="mt-2 h-2 w-full" />
        </li>
      ))}
    </ul>
  );
}

function HypothesesError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <Card className="border-red-200 ring-red-200">
      <CardHeader>
        <CardTitle>Couldn&apos;t load hypotheses</CardTitle>
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
