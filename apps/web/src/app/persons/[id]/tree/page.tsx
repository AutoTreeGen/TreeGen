"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";

import { PedigreeTree } from "@/components/pedigree-tree";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, fetchAncestors } from "@/lib/api";

const DEFAULT_GENERATIONS = 5;
const MIN_GENERATIONS = 1;
const MAX_GENERATIONS = 10;

export default function PersonTreePage() {
  const t = useTranslations("persons.tree");
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const personId = params.id;
  const requested = Number(searchParams.get("generations") ?? DEFAULT_GENERATIONS);
  const generations = Math.max(
    MIN_GENERATIONS,
    Math.min(MAX_GENERATIONS, Number.isFinite(requested) ? requested : DEFAULT_GENERATIONS),
  );

  const query = useQuery({
    queryKey: ["ancestors", personId, generations],
    queryFn: () => fetchAncestors(personId, generations),
    enabled: Boolean(personId),
  });

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <Button variant="ghost" size="sm" asChild>
            <Link href={`/persons/${personId}`}>← Back to person</Link>
          </Button>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
          {query.data ? (
            <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
              {query.data.root.primary_name ?? "Unnamed"} · loaded {query.data.generations_loaded}{" "}
              of {generations} requested generations
            </p>
          ) : null}
        </div>
        <p className="hidden text-xs text-[color:var(--color-ink-500)] md:block">
          drag to pan · scroll to zoom · click any node to recenter
        </p>
      </header>

      {query.isLoading ? <TreeLoadingSkeleton /> : null}

      {query.isError ? (
        <TreeErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {query.data ? (
        query.data.root.father === null && query.data.root.mother === null ? (
          <TreeEmptyState personId={personId} />
        ) : (
          <PedigreeTree root={query.data.root} />
        )
      ) : null}
    </main>
  );
}

// Пустое состояние: у корневой персоны нет ни отца, ни матери в данных.
function TreeEmptyState({ personId }: { personId: string }) {
  const t = useTranslations("persons.tree");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("noAncestors")}</CardTitle>
        <CardDescription>
          We don&apos;t have any parents linked to this person, so there&apos;s no tree to draw.
          Import a GEDCOM file or add parents manually to see the pedigree here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="primary" size="sm" asChild>
          <Link href={`/persons/${personId}`}>← Back to person</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function TreeLoadingSkeleton() {
  return (
    <div className="h-[70vh] w-full rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)] p-6">
      <div className="grid h-full grid-cols-1 gap-4 md:grid-cols-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    </div>
  );
}

function TreeErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load family tree</CardTitle>
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
