"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, fetchSources } from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

export default function SourcesListPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const treeId = params.id;
  const offset = Number(searchParams.get("offset") ?? "0") || 0;
  const initialQ = searchParams.get("q") ?? "";

  // Локальный state, синхронизируемый с URL через debounce: keystroke
  // даёт мгновенный отклик в input'е, запрос летит и URL обновляется
  // после паузы в SEARCH_DEBOUNCE_MS. Mirror Phase 4.4 persons-search shape.
  const [q, setQ] = useState(initialQ);
  const debouncedQ = useDebouncedValue(q, SEARCH_DEBOUNCE_MS);

  useEffect(() => {
    if (!treeId) return;
    const sp = new URLSearchParams();
    if (debouncedQ) sp.set("q", debouncedQ);
    const filterChanged = debouncedQ !== (searchParams.get("q") ?? "");
    if (!filterChanged && offset > 0) {
      sp.set("offset", String(offset));
    }
    const target = sp.toString();
    const current = searchParams.toString();
    if (target !== current) {
      router.replace(`/trees/${treeId}/sources${target ? `?${target}` : ""}`, { scroll: false });
    }
  }, [debouncedQ, offset, router, searchParams, treeId]);

  const query = useQuery({
    queryKey: ["sources", treeId, { q: debouncedQ, limit: PAGE_SIZE, offset }],
    queryFn: () =>
      fetchSources(treeId, {
        q: debouncedQ || undefined,
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
  const hasFilter = Boolean(debouncedQ);

  const setOffset = (next: number) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (next <= 0) {
      sp.delete("offset");
    } else {
      sp.set("offset", String(next));
    }
    const qs = sp.toString();
    router.push(`/trees/${treeId}/sources${qs ? `?${qs}` : ""}`);
  };

  const clearSearch = () => {
    setQ("");
    router.replace(`/trees/${treeId}/sources`, { scroll: false });
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">Tree</p>
          <h1 className="font-mono text-sm text-[color:var(--color-ink-700)]">{treeId}</h1>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">Sources</h2>
          {data ? (
            <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
              {total.toLocaleString("en-US")} {total === 1 ? "match" : "matches"}
              {total > 0 ? (
                <>
                  {" "}
                  · showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)}
                </>
              ) : null}
            </p>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← Persons</Link>
        </Button>
      </header>

      <section
        aria-label="Source search"
        className="mb-6 flex flex-col gap-3 rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 sm:flex-row sm:items-end"
      >
        <div className="flex flex-1 flex-col gap-1.5">
          <label
            htmlFor="sources-search"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Title, abbreviation, or author
          </label>
          <Input
            id="sources-search"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. Lubelskie, Bible, Anna Smith"
          />
        </div>
        <Button
          variant="ghost"
          size="md"
          onClick={clearSearch}
          disabled={!hasFilter}
          aria-label="Clear search"
        >
          Clear
        </Button>
      </section>

      {query.isLoading ? <SourcesListSkeleton /> : null}
      {query.isError ? (
        <SourcesListError error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {data ? (
        data.items.length === 0 ? (
          <SourcesEmptyState query={debouncedQ} onClear={clearSearch} />
        ) : (
          <ul className="space-y-3">
            {data.items.map((source) => (
              <li key={source.id}>
                <Card className="relative">
                  <CardHeader>
                    <CardTitle>
                      <Link
                        href={`/sources/${source.id}`}
                        className="after:absolute after:inset-0 after:rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2"
                      >
                        {source.title}
                      </Link>
                    </CardTitle>
                    <CardDescription className="flex flex-wrap items-center gap-2">
                      {source.author ? <span>by {source.author}</span> : null}
                      {source.publication ? (
                        <span className="text-[color:var(--color-ink-500)]">
                          · {source.publication}
                        </span>
                      ) : null}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-wrap items-center gap-2">
                    <Badge variant="neutral">{source.source_type}</Badge>
                    {source.abbreviation ? (
                      <Badge variant="outline">abbr: {source.abbreviation}</Badge>
                    ) : null}
                    {source.gedcom_xref ? (
                      <Badge variant="outline">{source.gedcom_xref}</Badge>
                    ) : null}
                    <Badge variant="accent">
                      {source.citation_count} citation
                      {source.citation_count === 1 ? "" : "s"}
                    </Badge>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )
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

function SourcesListSkeleton() {
  return (
    <ul className="space-y-3">
      {Array.from({ length: 6 }).map((_, idx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: статичный список без перестроек
        <li key={idx}>
          <Card>
            <CardHeader>
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="mt-2 h-4 w-1/2" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-4 w-1/3" />
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}

function SourcesEmptyState({ query, onClear }: { query: string; onClear: () => void }) {
  if (query) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Nothing found for &ldquo;{query}&rdquo;</CardTitle>
          <CardDescription>
            Try a shorter substring, or clear the search to see all sources.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="primary" size="sm" onClick={onClear}>
            Clear search
          </Button>
        </CardContent>
      </Card>
    );
  }
  return (
    <p className="text-sm text-[color:var(--color-ink-500)]">
      No sources imported yet. Sources appear as soon as a GEDCOM with{" "}
      <code className="font-mono text-xs">SOUR</code> records is loaded.
    </p>
  );
}

function SourcesListError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load sources</CardTitle>
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
