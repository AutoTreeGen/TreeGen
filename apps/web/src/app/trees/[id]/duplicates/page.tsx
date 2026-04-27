"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import { DuplicatePairCard } from "@/components/duplicate-pair-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type DuplicateEntityType, fetchDuplicateSuggestions } from "@/lib/api";
import { cn } from "@/lib/utils";

const ENTITY_TYPES: ReadonlyArray<DuplicateEntityType> = ["person", "source", "place"];
const MIN_CONFIDENCE = 0.6;
const MAX_CONFIDENCE = 0.95;
const STEP_CONFIDENCE = 0.05;
const DEFAULT_CONFIDENCE = 0.8;

function isEntityType(value: string | null): value is DuplicateEntityType {
  return value === "person" || value === "source" || value === "place";
}

function clampConfidence(raw: string | null): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_CONFIDENCE;
  return Math.max(MIN_CONFIDENCE, Math.min(MAX_CONFIDENCE, parsed));
}

export default function DuplicatesPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const treeId = params.id;

  const rawType = searchParams.get("type");
  const entityType: DuplicateEntityType = isEntityType(rawType) ? rawType : "person";
  const minConfidence = clampConfidence(searchParams.get("min_confidence"));

  const updateParam = (key: string, value: string) => {
    const sp = new URLSearchParams(searchParams.toString());
    sp.set(key, value);
    router.replace(`/trees/${treeId}/duplicates?${sp.toString()}`);
  };

  const query = useQuery({
    queryKey: ["duplicates", treeId, entityType, minConfidence],
    queryFn: () => fetchDuplicateSuggestions(treeId, entityType, minConfidence),
    enabled: Boolean(treeId),
  });

  const data = query.data;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← Back to persons</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Duplicate suggestions</h1>
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--color-ink-500)]">
          Read-only review of likely duplicates. No automatic merge — manual approval lands in Phase
          4.6 (CLAUDE.md §5: close-kin auto-merge is forbidden by design).
        </p>
      </header>

      <section className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <nav
          aria-label="Entity type"
          className="inline-flex rounded-lg bg-[color:var(--color-surface-muted)] p-1"
        >
          {ENTITY_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => updateParam("type", type)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2",
                entityType === type
                  ? "bg-[color:var(--color-surface)] text-[color:var(--color-ink-900)] shadow-sm"
                  : "text-[color:var(--color-ink-500)] hover:text-[color:var(--color-ink-900)]",
              )}
              aria-pressed={entityType === type}
            >
              {type === "person" ? "Persons" : type === "source" ? "Sources" : "Places"}
            </button>
          ))}
        </nav>

        <div className="flex flex-col gap-1 md:items-end">
          <label
            htmlFor="min-confidence"
            className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Min confidence: {minConfidence.toFixed(2)}
          </label>
          <input
            id="min-confidence"
            type="range"
            min={MIN_CONFIDENCE}
            max={MAX_CONFIDENCE}
            step={STEP_CONFIDENCE}
            value={minConfidence}
            onChange={(event) => updateParam("min_confidence", event.target.value)}
            className="w-56 accent-[color:var(--color-accent)]"
          />
          <span className="text-xs text-[color:var(--color-ink-500)]">
            ADR-0015 levels: ≥0.95 strong · 0.80–0.95 likely · 0.60–0.80 weak
          </span>
        </div>
      </section>

      {query.isLoading ? <DuplicatesSkeleton /> : null}
      {query.isError ? (
        <DuplicatesErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {data ? (
        <>
          <p className="mb-4 text-sm text-[color:var(--color-ink-500)]">
            {data.total === 0
              ? "No suggestions at this threshold — try lowering the slider."
              : `${data.total} ${entityType}${data.total === 1 ? "" : "s"} pair${
                  data.total === 1 ? "" : "s"
                } at min confidence ${minConfidence.toFixed(2)}.`}
          </p>
          <ul className="space-y-3">
            {data.items.map((pair) => (
              <li key={`${pair.entity_a_id}-${pair.entity_b_id}`}>
                <DuplicatePairCard pair={pair} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </main>
  );
}

function DuplicatesSkeleton() {
  return (
    <ul className="space-y-3">
      {Array.from({ length: 4 }).map((_, idx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: статичный список без перестроек
        <li key={idx}>
          <Card>
            <CardHeader>
              <Skeleton className="h-5 w-1/3" />
              <Skeleton className="mt-2 h-4 w-1/4" />
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
              </div>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}

function DuplicatesErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load duplicate suggestions</CardTitle>
        <CardDescription>{message}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-[color:var(--color-ink-500)]">
          Make sure parser-service is running on{" "}
          <code className="font-mono text-xs">http://localhost:8000</code> and CORS is enabled.
        </p>
      </CardContent>
      <CardContent>
        <Button variant="primary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}
