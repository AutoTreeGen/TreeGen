"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

import { QuayBadge } from "@/components/quay-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type SourceDetail, type SourceLinkedEntity, fetchSource } from "@/lib/api";

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>();
  const sourceId = params.id;

  const query = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => fetchSource(sourceId),
    enabled: Boolean(sourceId),
  });

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      {query.isLoading ? <SourceDetailSkeleton /> : null}
      {query.isError ? (
        <SourceDetailError error={query.error} onRetry={() => query.refetch()} />
      ) : null}
      {query.data ? <SourceDetailView source={query.data} /> : null}
    </main>
  );
}

function SourceDetailView({ source }: { source: SourceDetail }) {
  const persons = source.linked.filter((l) => l.table === "person");
  const families = source.linked.filter((l) => l.table === "family");
  const events = source.linked.filter((l) => l.table === "event");

  return (
    <article className="space-y-6">
      <header>
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${source.tree_id}/sources`}>← Back to sources</Link>
        </Button>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">{source.title}</h1>
        {source.author ? (
          <p className="mt-1 text-sm text-[color:var(--color-ink-700)]">by {source.author}</p>
        ) : null}
        <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-[color:var(--color-ink-500)]">
          {source.gedcom_xref ? (
            <span className="font-mono text-xs">{source.gedcom_xref}</span>
          ) : null}
          <Badge variant="neutral">{source.source_type}</Badge>
          {source.abbreviation ? (
            <Badge variant="outline">abbr: {source.abbreviation}</Badge>
          ) : null}
          <Badge variant="accent">
            {source.linked.length} citation{source.linked.length === 1 ? "" : "s"}
          </Badge>
        </p>
      </header>

      {source.publication || source.repository ? (
        <Card>
          <CardContent className="pt-5">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              {source.publication ? (
                <>
                  <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                    publication
                  </dt>
                  <dd>{source.publication}</dd>
                </>
              ) : null}
              {source.repository ? (
                <>
                  <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                    repository
                  </dt>
                  <dd>{source.repository}</dd>
                </>
              ) : null}
            </dl>
          </CardContent>
        </Card>
      ) : null}

      {source.text_excerpt ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Excerpt</CardTitle>
            <CardDescription>GEDCOM TEXT preserved at import.</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-[color:var(--color-ink-700)]">
              {source.text_excerpt}
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Separator />

      <LinkedSection
        title="Linked persons"
        emptyText="No person citations."
        items={persons}
        href={(id) => `/persons/${id}`}
      />
      <LinkedSection
        title="Linked events"
        emptyText="No event citations."
        items={events}
        // У event'ов нет dedicated UI-страницы в Phase 4.x — оставляем
        // только UUID + page-reference для traceability.
        href={null}
      />
      <LinkedSection
        title="Linked families"
        emptyText="No family citations."
        items={families}
        href={null}
      />
    </article>
  );
}

function LinkedSection({
  title,
  emptyText,
  items,
  href,
}: {
  title: string;
  emptyText: string;
  items: SourceLinkedEntity[];
  href: ((id: string) => string) | null;
}) {
  return (
    <section aria-label={title}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">{emptyText}</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((linked) => (
            <li key={`${linked.table}-${linked.id}-${linked.page ?? "no-page"}`}>
              <LinkedEntityCard linked={linked} href={href} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function LinkedEntityCard({
  linked,
  href,
}: {
  linked: SourceLinkedEntity;
  href: ((id: string) => string) | null;
}) {
  // display_label — приоритетный человекочитаемый label из бэка
  // (Phase 4.7-finalize). Если null (orphan FK / soft-delete) —
  // fallback на UUID, чтобы хоть какой-то идентификатор был виден.
  const primary = linked.display_label ?? linked.id;
  const showUuidSecondary = linked.display_label !== null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          {href ? (
            <Link
              href={href(linked.id)}
              className="underline-offset-4 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2"
            >
              {primary}
            </Link>
          ) : (
            <span>{primary}</span>
          )}
          <QuayBadge raw={linked.quay_raw} />
          <Badge variant="neutral">quality: {linked.quality.toFixed(2)}</Badge>
        </CardTitle>
        {showUuidSecondary ? (
          <CardDescription className="font-mono text-[11px] text-[color:var(--color-ink-500)]">
            {linked.id}
          </CardDescription>
        ) : null}
        {linked.page ? (
          <CardDescription>
            page: <span className="font-mono text-xs">{linked.page}</span>
          </CardDescription>
        ) : null}
      </CardHeader>
    </Card>
  );
}

function SourceDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div>
        <Skeleton className="h-8 w-32" />
        <Skeleton className="mt-4 h-9 w-2/3" />
        <Skeleton className="mt-3 h-4 w-1/2" />
      </div>
      <Separator />
      <div className="space-y-3">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    </div>
  );
}

function SourceDetailError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load source</CardTitle>
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
