"use client";

/**
 * Phase 6.3 — match detail: chromosome painting + link-to-person form.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ChromosomePainting } from "@/components/chromosome-painting";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import {
  type DnaMatchDetail,
  fetchDnaMatchDetail,
  linkDnaMatchToPerson,
  unlinkDnaMatch,
} from "@/lib/dna-api";

export default function DnaMatchDetailPage() {
  const params = useParams<{ matchId: string }>();
  const matchId = params.matchId;
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["dna-match", matchId],
    queryFn: () => fetchDnaMatchDetail(matchId),
    enabled: Boolean(matchId),
  });

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6 flex items-baseline justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
            DNA match
          </p>
          <h1 className="font-mono text-sm text-[color:var(--color-ink-700)]">{matchId}</h1>
        </div>
        {query.data ? (
          <Button variant="ghost" size="sm" asChild>
            <Link href={`/dna/${query.data.kit_id}/matches`}>← Back to matches</Link>
          </Button>
        ) : null}
      </header>

      {query.isLoading ? <DetailSkeleton /> : null}

      {query.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>Couldn&apos;t load match</CardTitle>
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

      {query.data ? (
        <DetailView
          match={query.data}
          onUpdate={(next) => queryClient.setQueryData(["dna-match", matchId], next)}
        />
      ) : null}
    </main>
  );
}

function DetailView({
  match,
  onUpdate,
}: {
  match: DnaMatchDetail;
  onUpdate: (next: DnaMatchDetail) => void;
}) {
  const t = useTranslations("dna.matchDetail");
  const display = match.display_name ?? match.external_match_id ?? `match ${match.id.slice(0, 8)}`;

  return (
    <article className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-2xl">
            {display}
            {match.predicted_relationship ? (
              <Badge variant="outline">{match.predicted_relationship}</Badge>
            ) : null}
            {match.matched_person_id ? <Badge variant="accent">linked</Badge> : null}
          </CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-3 text-sm">
            <Stat label="Total cM" value={fmtCm(match.total_cm)} />
            <Stat label="Longest" value={fmtCm(match.largest_segment_cm)} />
            <Stat label="Segments" value={match.segment_count?.toString() ?? "—"} />
            {match.confidence ? <Badge variant="neutral">{match.confidence}</Badge> : null}
          </CardDescription>
        </CardHeader>
        {match.shared_ancestor_hint ? (
          <CardContent>
            <p className="text-sm">
              <span className="text-[color:var(--color-ink-500)]">Possible shared ancestor: </span>
              <span className="font-medium">{match.shared_ancestor_hint.label}</span>
              {match.shared_ancestor_hint.person_id ? (
                <Link
                  href={`/persons/${match.shared_ancestor_hint.person_id}`}
                  className="ml-2 text-[color:var(--color-accent)] underline-offset-4 hover:underline"
                >
                  open profile →
                </Link>
              ) : null}
            </p>
          </CardContent>
        ) : null}
      </Card>

      <section aria-label="Chromosome painting">
        <h2 className="mb-2 text-lg font-semibold">{t("chromosomePainting")}</h2>
        {match.segments.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">
            No shared-segment data was imported for this match. Run a segments import (CSV) to see
            the chromosome painting.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
            <ChromosomePainting
              segments={match.segments}
              ariaLabel={`Shared DNA with ${display}`}
            />
          </div>
        )}
      </section>

      <LinkPersonSection match={match} onUpdate={onUpdate} />
    </article>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
        {label}:
      </span>
      <span className="font-mono">{value}</span>
    </span>
  );
}

function fmtCm(value: number | null): string {
  return value !== null ? `${value.toFixed(1)} cM` : "—";
}

function LinkPersonSection({
  match,
  onUpdate,
}: {
  match: DnaMatchDetail;
  onUpdate: (next: DnaMatchDetail) => void;
}) {
  const t = useTranslations("dna.matchDetail");
  const [personIdInput, setPersonIdInput] = useState("");

  const linkMutation = useMutation({
    mutationFn: (personId: string) =>
      linkDnaMatchToPerson(match.id, { tree_id: match.tree_id, person_id: personId }),
    onSuccess: (next) => {
      onUpdate(next);
      setPersonIdInput("");
    },
  });

  const unlinkMutation = useMutation({
    mutationFn: () => unlinkDnaMatch(match.id),
    onSuccess: (next) => {
      onUpdate(next);
    },
  });

  const isPending = linkMutation.isPending || unlinkMutation.isPending;
  const errorMessage = pickErrorMessage(linkMutation.error ?? unlinkMutation.error);

  return (
    <section
      aria-label="Link to person"
      className="rounded-md border border-[color:var(--color-border)] p-4"
    >
      <h2 className="text-lg font-semibold">{t("linkToPerson")}</h2>
      <p className="mt-1 text-xs text-[color:var(--color-ink-500)]">
        Linking attaches this match to an existing person record. The server refuses cross-tree
        links (privacy: ADR-0012).
      </p>

      {match.matched_person_id ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <p className="text-sm">
            Currently linked to{" "}
            <Link
              href={`/persons/${match.matched_person_id}`}
              className="font-mono text-[color:var(--color-accent)] underline-offset-4 hover:underline"
            >
              {match.matched_person_id.slice(0, 8)}
            </Link>
          </p>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => unlinkMutation.mutate()}
            disabled={isPending}
          >
            Unlink
          </Button>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const trimmed = personIdInput.trim();
            if (trimmed) linkMutation.mutate(trimmed);
          }}
          className="mt-3 flex flex-wrap items-end gap-3"
        >
          <label htmlFor="dna-link-person-id" className="flex flex-1 flex-col gap-1 text-xs">
            <span className="uppercase tracking-wide text-[color:var(--color-ink-500)]">
              Person UUID
            </span>
            <Input
              id="dna-link-person-id"
              type="text"
              placeholder="00000000-0000-0000-0000-000000000000"
              value={personIdInput}
              onChange={(e) => setPersonIdInput(e.target.value)}
              required
              minLength={36}
              maxLength={36}
              className="w-full max-w-md font-mono"
            />
          </label>
          <Button type="submit" variant="primary" size="sm" disabled={isPending}>
            Link match
          </Button>
        </form>
      )}

      {errorMessage ? <p className="mt-2 text-sm text-red-600">{errorMessage}</p> : null}
    </section>
  );
}

function pickErrorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return null;
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}
