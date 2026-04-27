"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, fetchPersons } from "@/lib/api";

const PAGE_SIZE = 50;

export default function PersonsListPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const treeId = params.id;
  const offset = Number(searchParams.get("offset") ?? "0") || 0;

  const query = useQuery({
    queryKey: ["persons", treeId, { limit: PAGE_SIZE, offset }],
    queryFn: () => fetchPersons(treeId, PAGE_SIZE, offset),
    enabled: Boolean(treeId),
  });

  const data = query.data;
  const total = data?.total ?? 0;
  const lastPageOffset = total > 0 ? Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE : 0;
  const canPrev = offset > 0;
  const canNext = total > 0 && offset + PAGE_SIZE < total;

  const setOffset = (next: number) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (next <= 0) {
      sp.delete("offset");
    } else {
      sp.set("offset", String(next));
    }
    const qs = sp.toString();
    router.push(`/trees/${treeId}/persons${qs ? `?${qs}` : ""}`);
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">Tree</p>
          <h1 className="font-mono text-sm text-[color:var(--color-ink-700)]">{treeId}</h1>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">Persons</h2>
          {data ? (
            <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
              {total.toLocaleString("en-US")} total · showing {offset + 1}–
              {Math.min(offset + PAGE_SIZE, total)}
            </p>
          ) : null}
        </div>
        {/* Без pending-count: dedup-scoring проходит по всему дереву и при
            61k персон занимает секунды. Запускать его на каждый рендер
            списка персон — регрессия. Точное число пар видно на самой
            странице duplicates после применения slider'ом порога. */}
        <Button variant="secondary" size="md" asChild>
          <Link href={`/trees/${treeId}/duplicates`}>Review duplicates →</Link>
        </Button>
      </header>

      {query.isLoading ? <PersonsListSkeleton /> : null}

      {query.isError ? (
        <PersonsListError error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {data ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {data.items.map((person) => (
            <Card key={person.id} className="relative">
              <CardHeader>
                <CardTitle>
                  {/* Основная ссылка карточки растянута через ::after — клик
                      по любому неинтерактивному месту ведёт на `/persons/[id]`.
                      Кнопка «Tree» лежит поверх через z-index. */}
                  <Link
                    href={`/persons/${person.id}`}
                    className="after:absolute after:inset-0 after:rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2"
                  >
                    {person.primary_name ?? "Unnamed"}
                  </Link>
                </CardTitle>
                <CardDescription>
                  {[
                    person.gedcom_xref,
                    person.sex !== "U" ? `sex: ${person.sex}` : null,
                    `confidence: ${person.confidence_score.toFixed(2)}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-between gap-2">
                <p className="font-mono text-xs text-[color:var(--color-ink-500)]">{person.id}</p>
                <Button variant="secondary" size="sm" asChild className="relative z-10">
                  <Link
                    href={`/persons/${person.id}/tree`}
                    aria-label={`View family tree for ${person.primary_name ?? "this person"}`}
                  >
                    Tree
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      {data ? (
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

function PersonsListSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 9 }).map((_, idx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: статичный список без перестроек
        <Card key={idx}>
          <CardHeader>
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="mt-2 h-4 w-1/2" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-3 w-3/4" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function PersonsListError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load persons</CardTitle>
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
