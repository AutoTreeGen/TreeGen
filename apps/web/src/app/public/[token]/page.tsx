"use client";

/**
 * /public/[token] — публичная read-only страница дерева (Phase 11.2).
 *
 * Доступна без аутентификации; токен в URL'е резолвится server-side.
 * DNA-данные не отображаются вообще; persons-likely-alive показаны как
 * «Living relative» без дат. См. ADR-0047 §«Privacy».
 *
 * Для MVP — простой list view: имя + годы жизни + sex. Pedigree-graph
 * рендеринг — Phase 11.2.1 (если потребуется UX-обоснованно).
 */

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, type PublicTreePerson, fetchPublicTreeView } from "@/lib/api";

export default function PublicTreePage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const view = useQuery({
    queryKey: ["public-tree", token],
    queryFn: () => fetchPublicTreeView(token),
    retry: false,
    refetchOnWindowFocus: false,
  });

  if (view.isLoading) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>
      </main>
    );
  }

  if (view.isError) {
    const isNotFound = view.error instanceof ApiError && view.error.status === 404;
    const isRateLimit = view.error instanceof ApiError && view.error.status === 429;
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <Card>
          <CardHeader>
            <CardTitle>{isNotFound ? "Link not available" : "Could not load tree"}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[color:var(--color-ink-500)]">
              {isNotFound
                ? "This public share link has been revoked, expired, or never existed."
                : isRateLimit
                  ? "Too many requests from your network. Please try again in a minute."
                  : "Something went wrong while loading the tree."}
            </p>
          </CardContent>
        </Card>
      </main>
    );
  }

  const data = view.data;
  if (!data) return null;

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="public-tree-name">
          {data.tree_name}
        </h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          {data.person_count.toLocaleString()} persons · public read-only view (DNA data and living
          relatives are private).
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Persons</CardTitle>
        </CardHeader>
        <CardContent>
          {data.persons.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No persons in this tree.</p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-border)]">
              {data.persons.map((p) => (
                <PersonRow key={p.id} person={p} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

function PersonRow({ person }: { person: PublicTreePerson }) {
  const lifespan = formatLifespan(person.birth_year, person.death_year);
  return (
    <li className="flex flex-wrap items-center gap-3 py-3" data-testid={`person-${person.id}`}>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{person.display_name}</p>
        {lifespan ? (
          <p className="truncate text-xs text-[color:var(--color-ink-500)]">{lifespan}</p>
        ) : null}
      </div>
      {person.is_anonymized ? (
        <Badge variant="neutral" data-testid="anonymized-badge">
          Living
        </Badge>
      ) : null}
    </li>
  );
}

function formatLifespan(birth: number | null, death: number | null): string | null {
  if (birth === null && death === null) return null;
  return `${birth ?? "?"} – ${death ?? "?"}`;
}
