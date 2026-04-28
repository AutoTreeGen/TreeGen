"use client";

/**
 * Phase 6.3 — DNA dashboard: список китов пользователя.
 *
 * Без auth (Phase 6.x): user_id берётся из query-параметра ``?user=<uuid>``,
 * fallback — ``NEXT_PUBLIC_DEMO_DNA_USER_ID``. Когда auth подключим
 * в Phase 4.2/6.x — заменим обе fallback-логики на session-based.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { type DnaKitSummary, fetchDnaKits } from "@/lib/dna-api";

const FALLBACK_USER_ID = process.env.NEXT_PUBLIC_DEMO_DNA_USER_ID ?? "";

export default function DnaHomePage() {
  // Next 15 требует Suspense-boundary вокруг useSearchParams() в client
  // page'ах, иначе static export падает (см. dynamic-IO docs).
  return (
    <Suspense fallback={<KitsSkeleton />}>
      <DnaHomeContent />
    </Suspense>
  );
}

function DnaHomeContent() {
  const searchParams = useSearchParams();
  const userId = searchParams.get("user") ?? FALLBACK_USER_ID;

  const query = useQuery({
    queryKey: ["dna-kits", userId],
    queryFn: () => fetchDnaKits(userId),
    enabled: Boolean(userId),
  });

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight">DNA kits</h1>
        <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">
          Choose a kit to see its DNA matches and link them to people in your tree.
        </p>
      </header>

      {!userId ? <NoUserSelected /> : null}

      {userId && query.isLoading ? <KitsSkeleton /> : null}

      {userId && query.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>Couldn&apos;t load kits</CardTitle>
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

      {userId && query.data ? <KitsList items={query.data.items} /> : null}
    </main>
  );
}

function KitsList({ items }: { items: DnaKitSummary[] }) {
  if (items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No kits yet</CardTitle>
          <CardDescription>
            Upload a DNA kit through the import endpoint, then revisit this page.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  return (
    <ul className="flex flex-col gap-3">
      {items.map((kit) => (
        <li key={kit.id}>
          <KitCard kit={kit} />
        </li>
      ))}
    </ul>
  );
}

function KitCard({ kit }: { kit: DnaKitSummary }) {
  const display = kit.display_name ?? kit.external_kit_id ?? `Kit ${kit.id.slice(0, 8)}`;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <Link
            href={`/dna/${kit.id}/matches`}
            className="underline-offset-4 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
          >
            {display}
          </Link>
          <Badge variant="outline">{kit.source_platform}</Badge>
          {kit.ethnicity_population !== "general" ? (
            <Badge variant="neutral">{kit.ethnicity_population}</Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          tree <span className="font-mono text-xs">{kit.tree_id.slice(0, 8)}</span>
          {kit.person_id ? (
            <>
              {" · linked to "}
              <Link
                href={`/persons/${kit.person_id}`}
                className="font-mono text-xs underline-offset-4 hover:underline"
              >
                {kit.person_id.slice(0, 8)}
              </Link>
            </>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="primary" size="sm" asChild>
          <Link href={`/dna/${kit.id}/matches`}>View matches →</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function NoUserSelected() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>No user selected</CardTitle>
        <CardDescription>
          Pass <code>?user=&lt;uuid&gt;</code> in the URL or set{" "}
          <code className="text-xs">NEXT_PUBLIC_DEMO_DNA_USER_ID</code>. Auth wiring lands in Phase
          4.2 / 6.x.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function KitsSkeleton() {
  return (
    <ul className="flex flex-col gap-3">
      <li>
        <Skeleton className="h-24 w-full" />
      </li>
      <li>
        <Skeleton className="h-24 w-full" />
      </li>
    </ul>
  );
}
