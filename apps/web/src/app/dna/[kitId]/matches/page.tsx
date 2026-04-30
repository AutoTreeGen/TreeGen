"use client";

/**
 * Phase 6.3 — list matches for one DNA kit.
 *
 * Sortable table (backend всегда сортирует total_cm DESC; client-side
 * сортировка — следующая итерация). Filters: ``min_cm`` slider/input
 * и ``predicted`` substring. Пагинация limit/offset.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { type DnaMatchListItem, fetchDnaMatches } from "@/lib/dna-api";

const PAGE_SIZE = 50;

export default function DnaKitMatchesPage() {
  const t = useTranslations("dna.kitMatches");
  const params = useParams<{ kitId: string }>();
  const kitId = params.kitId;
  const [minCmInput, setMinCmInput] = useState<string>("20");
  const [predictedInput, setPredictedInput] = useState<string>("");
  const [offset, setOffset] = useState(0);

  // Активные filter-значения, уходящие в API; меняются только при «Apply»,
  // чтобы каждый keystroke не запускал запрос.
  const [activeFilters, setActiveFilters] = useState<{
    minCm: number | null;
    predicted: string | null;
  }>({ minCm: 20, predicted: null });

  const query = useQuery({
    queryKey: ["dna-matches", kitId, activeFilters, offset],
    queryFn: () =>
      fetchDnaMatches(kitId, {
        limit: PAGE_SIZE,
        offset,
        minCm: activeFilters.minCm,
        predicted: activeFilters.predicted,
      }),
    enabled: Boolean(kitId),
  });

  const applyFilters = () => {
    const parsed = Number.parseFloat(minCmInput);
    setActiveFilters({
      minCm: Number.isFinite(parsed) && parsed >= 0 ? parsed : null,
      predicted: predictedInput.trim() || null,
    });
    setOffset(0);
  };

  const total = query.data?.total ?? 0;
  const items = query.data?.items ?? [];
  const showingTo = Math.min(offset + items.length, total);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
            kit <span className="font-mono text-xs">{kitId.slice(0, 8)}</span>
          </p>
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/dna">← All kits</Link>
        </Button>
      </header>

      <FiltersRow
        minCmInput={minCmInput}
        setMinCmInput={setMinCmInput}
        predictedInput={predictedInput}
        setPredictedInput={setPredictedInput}
        onApply={applyFilters}
        disabled={query.isFetching}
      />

      {query.isLoading ? <TableSkeleton /> : null}

      {query.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>Couldn&apos;t load matches</CardTitle>
            <CardDescription>
              {query.error instanceof ApiError
                ? `${query.error.status}: ${query.error.message}`
                : "Unknown error"}
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {query.data ? (
        <>
          <p className="mt-4 text-xs text-[color:var(--color-ink-500)]">
            Showing {items.length === 0 ? 0 : offset + 1}–{showingTo} of {total} matches
          </p>
          <MatchesTable items={items} />
          <PaginationRow
            offset={offset}
            pageSize={PAGE_SIZE}
            total={total}
            disabled={query.isFetching}
            onPrev={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
            onNext={() => setOffset((prev) => prev + PAGE_SIZE)}
          />
        </>
      ) : null}
    </main>
  );
}

function FiltersRow({
  minCmInput,
  setMinCmInput,
  predictedInput,
  setPredictedInput,
  onApply,
  disabled,
}: {
  minCmInput: string;
  setMinCmInput: (next: string) => void;
  predictedInput: string;
  setPredictedInput: (next: string) => void;
  onApply: () => void;
  disabled: boolean;
}) {
  const t = useTranslations("dna.kitMatches");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onApply();
      }}
      className="flex flex-wrap items-end gap-3 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4"
    >
      <label htmlFor="dna-min-cm" className="flex flex-col gap-1 text-xs">
        <span className="uppercase tracking-wide text-[color:var(--color-ink-500)]">
          {t("minCm")}
        </span>
        <Input
          id="dna-min-cm"
          type="number"
          min={0}
          step="0.5"
          value={minCmInput}
          onChange={(e) => setMinCmInput(e.target.value)}
          className="w-28"
        />
      </label>
      <label htmlFor="dna-predicted" className="flex flex-col gap-1 text-xs">
        <span className="uppercase tracking-wide text-[color:var(--color-ink-500)]">
          Predicted relationship
        </span>
        <Input
          id="dna-predicted"
          type="text"
          placeholder="e.g. cousin"
          value={predictedInput}
          onChange={(e) => setPredictedInput(e.target.value)}
          className="w-48"
        />
      </label>
      <Button type="submit" variant="primary" size="sm" disabled={disabled}>
        Apply filters
      </Button>
    </form>
  );
}

function MatchesTable({ items }: { items: DnaMatchListItem[] }) {
  const t = useTranslations("dna.kitMatches");
  if (items.length === 0) {
    return (
      <p className="mt-6 text-sm text-[color:var(--color-ink-500)]">
        No matches found for the current filters.
      </p>
    );
  }
  return (
    <>
      {/* Phase 4.14a — mobile (<sm): card stack; ≥sm: вернуть таблицу.
          Семантика остаётся (table headers + sortable cells будут добавлены
          в 6.5), card-вариант повторяет ту же информацию в сжатом виде. */}
      <ul
        className="mt-4 flex flex-col gap-2 sm:hidden"
        aria-label="DNA matches"
        data-testid="dna-matches-card-list"
      >
        {items.map((m) => (
          <li
            key={m.id}
            className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">
                  {m.display_name ?? <span className="text-[color:var(--color-ink-500)]">—</span>}
                </p>
                {m.external_match_id ? (
                  <p className="truncate font-mono text-[11px] text-[color:var(--color-ink-500)]">
                    {m.external_match_id}
                  </p>
                ) : null}
              </div>
              {m.matched_person_id ? (
                <Badge variant="accent">linked</Badge>
              ) : (
                <Badge variant="outline">unlinked</Badge>
              )}
            </div>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
              <div>
                <dt className="text-[color:var(--color-ink-500)]">{t("totalCm")}</dt>
                <dd className="font-mono">{m.total_cm !== null ? m.total_cm.toFixed(1) : "—"}</dd>
              </div>
              <div>
                <dt className="text-[color:var(--color-ink-500)]">Longest</dt>
                <dd className="font-mono">
                  {m.largest_segment_cm !== null ? m.largest_segment_cm.toFixed(1) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-[color:var(--color-ink-500)]">Segments</dt>
                <dd className="font-mono">{m.segment_count !== null ? m.segment_count : "—"}</dd>
              </div>
            </dl>
            <div className="mt-3 flex items-center justify-between gap-2">
              <span className="truncate text-xs text-[color:var(--color-ink-700)]">
                {m.predicted_relationship ?? (
                  <span className="text-[color:var(--color-ink-500)]">—</span>
                )}
              </span>
              <Button variant="secondary" size="sm" asChild>
                <Link href={`/dna/matches/${m.id}`}>Open →</Link>
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-4 hidden overflow-x-auto rounded-md border border-[color:var(--color-border)] sm:block">
        <table className="min-w-full text-sm">
          <thead className="bg-[color:var(--color-surface-muted)] text-left text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">{t("totalCm")}</th>
              <th className="px-3 py-2">Longest</th>
              <th className="px-3 py-2">Segments</th>
              <th className="px-3 py-2">Predicted</th>
              <th className="px-3 py-2">Linked</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.id} className="border-t border-[color:var(--color-border)]">
                <td className="px-3 py-2">
                  <div className="font-medium">
                    {m.display_name ?? <span className="text-[color:var(--color-ink-500)]">—</span>}
                  </div>
                  {m.external_match_id ? (
                    <div className="font-mono text-[11px] text-[color:var(--color-ink-500)]">
                      {m.external_match_id}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2 font-mono">
                  {m.total_cm !== null ? m.total_cm.toFixed(1) : "—"}
                </td>
                <td className="px-3 py-2 font-mono">
                  {m.largest_segment_cm !== null ? m.largest_segment_cm.toFixed(1) : "—"}
                </td>
                <td className="px-3 py-2 font-mono">
                  {m.segment_count !== null ? m.segment_count : "—"}
                </td>
                <td className="px-3 py-2">
                  {m.predicted_relationship ?? (
                    <span className="text-[color:var(--color-ink-500)]">—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {m.matched_person_id ? (
                    <Badge variant="accent">linked</Badge>
                  ) : (
                    <Badge variant="outline">unlinked</Badge>
                  )}
                </td>
                <td className="px-3 py-2">
                  <Button variant="link" size="sm" asChild>
                    <Link href={`/dna/matches/${m.id}`}>Open →</Link>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function PaginationRow({
  offset,
  pageSize,
  total,
  disabled,
  onPrev,
  onNext,
}: {
  offset: number;
  pageSize: number;
  total: number;
  disabled: boolean;
  onPrev: () => void;
  onNext: () => void;
}) {
  const hasPrev = offset > 0;
  const hasNext = offset + pageSize < total;
  return (
    <div className="mt-4 flex items-center justify-end gap-2">
      <Button variant="secondary" size="sm" onClick={onPrev} disabled={!hasPrev || disabled}>
        ← Prev
      </Button>
      <Button variant="secondary" size="sm" onClick={onNext} disabled={!hasNext || disabled}>
        Next →
      </Button>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="mt-4 flex flex-col gap-2">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
