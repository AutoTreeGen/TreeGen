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
import {
  ApiError,
  type EventSummary,
  type NameSummary,
  type PersonCitationDetail,
  type PersonDetail,
  fetchPerson,
  fetchPersonCitations,
} from "@/lib/api";

export default function PersonDetailPage() {
  const params = useParams<{ id: string }>();
  const personId = params.id;

  const query = useQuery({
    queryKey: ["person", personId],
    queryFn: () => fetchPerson(personId),
    enabled: Boolean(personId),
  });

  // Citations грузим отдельным query'ем, чтобы блок Sources/citations не
  // блокировал рендер карточки персоны (это extra round-trip к
  // /persons/{id}/citations, который и так может быть сравнительно
  // тяжёлым на больших деревьях).
  const citationsQuery = useQuery({
    queryKey: ["person-citations", personId],
    queryFn: () => fetchPersonCitations(personId),
    enabled: Boolean(personId),
  });

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      {query.isLoading ? <PersonDetailSkeleton /> : null}

      {query.isError ? (
        <PersonDetailError error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {query.data ? (
        <PersonDetailView
          person={query.data}
          citations={citationsQuery.data?.items ?? null}
          citationsLoading={citationsQuery.isLoading}
          citationsError={citationsQuery.isError}
        />
      ) : null}
    </main>
  );
}

function PersonDetailView({
  person,
  citations,
  citationsLoading,
  citationsError,
}: {
  person: PersonDetail;
  citations: PersonCitationDetail[] | null;
  citationsLoading: boolean;
  citationsError: boolean;
}) {
  const primaryName = person.names.find((name) => name.sort_order === 0) ?? person.names[0] ?? null;
  const primaryDisplay = primaryName ? formatName(primaryName) : "Unnamed";
  const otherNames = person.names.filter((name) => name !== primaryName);
  const sortedEvents = [...person.events].sort((a, b) => {
    const aDate = a.date_start ?? a.date_end ?? "";
    const bDate = b.date_start ?? b.date_end ?? "";
    return aDate.localeCompare(bDate);
  });

  return (
    <article className="space-y-6">
      <header>
        <div className="flex items-center justify-between gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link href={`/trees/${person.tree_id}/persons`}>← Back to tree</Link>
          </Button>
          <Button variant="primary" size="sm" asChild>
            <Link href={`/persons/${person.id}/tree`}>View family tree →</Link>
          </Button>
        </div>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">{primaryDisplay}</h1>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-[color:var(--color-ink-500)]">
          {person.gedcom_xref ? <span className="font-mono">{person.gedcom_xref}</span> : null}
          {person.sex !== "U" ? <Badge variant="outline">sex: {person.sex}</Badge> : null}
          <Badge variant="outline">status: {person.status}</Badge>
          <Badge variant="neutral">confidence: {person.confidence_score.toFixed(2)}</Badge>
        </p>
      </header>

      <Separator />

      <section aria-labelledby="names-heading">
        <h2 id="names-heading" className="text-lg font-semibold">
          Names
        </h2>
        {otherNames.length === 0 ? (
          <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">
            No alternative names recorded.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {otherNames.map((name) => (
              <li key={name.id}>
                <Card>
                  <CardHeader>
                    <CardTitle>{formatName(name)}</CardTitle>
                    <CardDescription>
                      <Badge variant="neutral">sort: {name.sort_order}</Badge>
                    </CardDescription>
                  </CardHeader>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Separator />

      <section aria-labelledby="events-heading">
        <h2 id="events-heading" className="text-lg font-semibold">
          Events
        </h2>
        {sortedEvents.length === 0 ? (
          <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">No events recorded.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {sortedEvents.map((event) => (
              <li key={event.id}>
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Badge variant="accent">{event.event_type}</Badge>
                      <span className="text-sm font-normal text-[color:var(--color-ink-700)]">
                        {formatEventDate(event)}
                      </span>
                    </CardTitle>
                    {event.place_id ? (
                      <CardDescription>
                        place: <span className="font-mono text-xs">{event.place_id}</span>
                      </CardDescription>
                    ) : null}
                  </CardHeader>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Separator />

      <section aria-labelledby="sources-heading">
        <h2 id="sources-heading" className="text-lg font-semibold">
          Sources
        </h2>
        <PersonSources citations={citations} loading={citationsLoading} hasError={citationsError} />
      </section>
    </article>
  );
}

function PersonSources({
  citations,
  loading,
  hasError,
}: {
  citations: PersonCitationDetail[] | null;
  loading: boolean;
  hasError: boolean;
}) {
  if (loading) {
    return (
      <ul className="mt-3 space-y-2">
        <li>
          <Skeleton className="h-16 w-full" />
        </li>
        <li>
          <Skeleton className="h-16 w-full" />
        </li>
      </ul>
    );
  }
  if (hasError) {
    return (
      <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">
        Couldn&apos;t load citations — try refreshing the page.
      </p>
    );
  }
  if (!citations || citations.length === 0) {
    return <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">No citations recorded.</p>;
  }
  return (
    <ul className="mt-3 space-y-2">
      {citations.map((citation) => (
        <li key={citation.id}>
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                <Link
                  href={`/sources/${citation.source_id}`}
                  className="underline-offset-4 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2"
                >
                  {citation.source_title}
                </Link>
                {citation.source_abbreviation ? (
                  <Badge variant="outline">{citation.source_abbreviation}</Badge>
                ) : null}
                <QuayBadge raw={citation.quay_raw} />
              </CardTitle>
              <CardDescription className="flex flex-wrap items-center gap-2">
                <Badge variant="neutral">
                  {citation.entity_type === "person" ? "person-level" : "event-level"}
                </Badge>
                {citation.event_type ? (
                  <Badge variant="outline">event: {citation.event_type}</Badge>
                ) : null}
                {citation.role ? <Badge variant="outline">role: {citation.role}</Badge> : null}
                {citation.page ? (
                  <span className="text-[color:var(--color-ink-500)]">page: {citation.page}</span>
                ) : null}
              </CardDescription>
            </CardHeader>
            {citation.quoted_text || citation.note ? (
              <CardContent>
                {citation.quoted_text ? (
                  <p className="whitespace-pre-wrap text-sm text-[color:var(--color-ink-700)]">
                    “{citation.quoted_text}”
                  </p>
                ) : null}
                {citation.note ? (
                  <p className="mt-1 text-xs italic text-[color:var(--color-ink-500)]">
                    {citation.note}
                  </p>
                ) : null}
              </CardContent>
            ) : null}
          </Card>
        </li>
      ))}
    </ul>
  );
}

function PersonDetailSkeleton() {
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

function PersonDetailError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Couldn&apos;t load person</CardTitle>
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

function formatName(name: NameSummary): string {
  const parts = [name.given_name, name.surname].filter(Boolean);
  return parts.length === 0 ? "Unnamed" : parts.join(" ");
}

function formatEventDate(event: EventSummary): string {
  const start = formatIso(event.date_start);
  const end = formatIso(event.date_end);
  if (event.date_raw) return event.date_raw;
  if (start && end && start !== end) return `${start} – ${end}`;
  if (start) return start;
  if (end) return end;
  return "date unknown";
}

function formatIso(value: string | null): string | null {
  if (!value) return null;
  // date_start/date_end приходят ISO 8601 (e.g. "1850-04-12T00:00:00").
  // Берём только календарную часть; форматирование локали — позже в Phase 4.5.
  const datePart = value.split("T")[0] ?? value;
  return datePart;
}
