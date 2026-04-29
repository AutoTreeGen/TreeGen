"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type TopSurname,
  type TreeStatisticsResponse,
  fetchTreeStatistics,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Phase 6.5 — Tree statistics dashboard.
 *
 * Маршрут: ``/trees/{id}/stats``. Read-only view с агрегатами от
 * ``GET /trees/{id}/statistics`` (ADR-0051): 7 counts + oldest birth year +
 * pedigree depth + top-10 surnames. Простые CSS-bar charts (без recharts).
 */
export default function TreeStatsPage() {
  const t = useTranslations("trees.stats");
  const params = useParams<{ id: string }>();
  const treeId = params.id;

  const query = useQuery({
    queryKey: ["tree-stats", treeId],
    queryFn: () => fetchTreeStatistics(treeId),
    enabled: Boolean(treeId),
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← {t("backToTree")}</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
      </header>

      {query.isLoading ? <StatsSkeleton /> : null}

      {query.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("loadError")}</CardTitle>
            <CardDescription>
              {query.error instanceof ApiError
                ? `${query.error.status}: ${query.error.message}`
                : (query.error as Error)?.message}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="primary" size="sm" onClick={() => query.refetch()}>
              {t("retry")}
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {query.data ? <StatsView stats={query.data} /> : null}
    </main>
  );
}

function StatsView({ stats }: { stats: TreeStatisticsResponse }) {
  const t = useTranslations("trees.stats");

  return (
    <>
      <section aria-labelledby="counts-heading" className="mb-8" data-testid="stats-counts-section">
        <h2 id="counts-heading" className="sr-only">
          {t("counts.heading")}
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
          <StatCard label={t("counts.persons")} value={stats.persons_count} />
          <StatCard label={t("counts.families")} value={stats.families_count} />
          <StatCard label={t("counts.events")} value={stats.events_count} />
          <StatCard label={t("counts.sources")} value={stats.sources_count} />
          <StatCard label={t("counts.hypotheses")} value={stats.hypotheses_count} />
          <StatCard label={t("counts.dnaMatches")} value={stats.dna_matches_count} />
          <StatCard label={t("counts.places")} value={stats.places_count} />
          <StatCard
            label={t("counts.pedigreeDepth")}
            value={stats.pedigree_max_depth}
            hint={t("counts.pedigreeDepthHint")}
          />
        </div>
      </section>

      <section aria-labelledby="oldest-heading" className="mb-8">
        <Card>
          <CardHeader>
            <CardTitle id="oldest-heading" className="text-sm">
              {t("oldest.title")}
            </CardTitle>
            <CardDescription>{t("oldest.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {stats.oldest_birth_year === null ? (
              <p className="text-sm text-[color:var(--color-ink-500)]">{t("oldest.empty")}</p>
            ) : (
              <p className="text-3xl font-semibold tabular-nums" data-testid="oldest-year">
                {stats.oldest_birth_year}
              </p>
            )}
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="surnames-heading">
        <Card>
          <CardHeader>
            <CardTitle id="surnames-heading" className="text-sm">
              {t("surnames.title")}
            </CardTitle>
            <CardDescription>{t("surnames.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {stats.top_surnames.length === 0 ? (
              <p className="text-sm text-[color:var(--color-ink-500)]">{t("surnames.empty")}</p>
            ) : (
              <SurnamesBarChart surnames={stats.top_surnames} />
            )}
          </CardContent>
        </Card>
      </section>
    </>
  );
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <Card data-testid="stat-card">
      <CardContent className="py-4">
        <p className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
        {hint ? <p className="mt-1 text-[11px] text-[color:var(--color-ink-500)]">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

function SurnamesBarChart({ surnames }: { surnames: TopSurname[] }) {
  const max = surnames.reduce((acc, row) => Math.max(acc, row.person_count), 0);
  return (
    <ul className="space-y-2" data-testid="surnames-list">
      {surnames.map((row, index) => {
        const width = max === 0 ? 0 : Math.round((row.person_count / max) * 100);
        return (
          <li key={row.surname} className="flex items-center gap-3 text-sm">
            <span className="w-6 text-right font-mono text-[11px] text-[color:var(--color-ink-500)]">
              {index + 1}.
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">{row.surname}</span>
            <div
              className="relative h-5 flex-[3] overflow-hidden rounded bg-[color:var(--color-surface-muted)]"
              aria-hidden="true"
            >
              <div
                className={cn("h-full rounded bg-[color:var(--color-accent)] transition-[width]")}
                style={{ width: `${width}%` }}
              />
            </div>
            <Badge variant="neutral" className="tabular-nums">
              {row.person_count.toLocaleString()}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

// Stable keys для skeleton placeholder'ов: восемь имён по числу stat-карт.
const SKELETON_KEYS = [
  "persons",
  "families",
  "events",
  "sources",
  "hypotheses",
  "dna",
  "places",
  "depth",
] as const;

function StatsSkeleton() {
  return (
    <>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {SKELETON_KEYS.map((key) => (
          <Skeleton key={key} className="h-20 w-full" />
        ))}
      </div>
      <Skeleton className="mb-6 h-32 w-full" />
      <Skeleton className="h-64 w-full" />
    </>
  );
}
