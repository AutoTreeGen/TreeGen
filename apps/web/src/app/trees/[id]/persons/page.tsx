"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, fetchHypotheses, searchPersons } from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Преобразовать строковое поле года в число для API. Пустую/невалидную
 * строку отдаём undefined — эндпоинт пропускает фильтр.
 */
function parseYear(raw: string): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 1 && n <= 9999 ? n : undefined;
}

export default function PersonsListPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const treeId = params.id;

  const initialQ = searchParams.get("q") ?? "";
  const initialMin = searchParams.get("birth_year_min") ?? "";
  const initialMax = searchParams.get("birth_year_max") ?? "";
  const initialPhonetic = searchParams.get("phonetic") === "true";
  const offset = Number(searchParams.get("offset") ?? "0") || 0;

  // Локальный state, синхронизируемый с URL через debounce: пользователь
  // печатает → state обновляется мгновенно (controlled input ощущается
  // живым), запрос летит и URL обновляется через 300 мс.
  const [q, setQ] = useState(initialQ);
  const [birthYearMin, setBirthYearMin] = useState(initialMin);
  const [birthYearMax, setBirthYearMax] = useState(initialMax);
  const [phonetic, setPhonetic] = useState(initialPhonetic);

  const debouncedQ = useDebouncedValue(q, SEARCH_DEBOUNCE_MS);
  const debouncedMin = useDebouncedValue(birthYearMin, SEARCH_DEBOUNCE_MS);
  const debouncedMax = useDebouncedValue(birthYearMax, SEARCH_DEBOUNCE_MS);

  const minYear = useMemo(() => parseYear(debouncedMin), [debouncedMin]);
  const maxYear = useMemo(() => parseYear(debouncedMax), [debouncedMax]);

  // Sync URL state when debounced values change. router.replace вместо
  // push — чтобы back-кнопка не прыгала через каждый keystroke. Page
  // reset (offset=0) при смене фильтра, иначе страница 5 с 0 результатов.
  useEffect(() => {
    if (!treeId) return;
    const sp = new URLSearchParams();
    if (debouncedQ) sp.set("q", debouncedQ);
    if (phonetic) sp.set("phonetic", "true");
    if (debouncedMin) sp.set("birth_year_min", debouncedMin);
    if (debouncedMax) sp.set("birth_year_max", debouncedMax);
    const filtersChanged =
      debouncedQ !== (searchParams.get("q") ?? "") ||
      phonetic !== (searchParams.get("phonetic") === "true") ||
      debouncedMin !== (searchParams.get("birth_year_min") ?? "") ||
      debouncedMax !== (searchParams.get("birth_year_max") ?? "");
    if (!filtersChanged && offset > 0) {
      sp.set("offset", String(offset));
    }
    const target = sp.toString();
    const current = searchParams.toString();
    if (target !== current) {
      router.replace(`/trees/${treeId}/persons${target ? `?${target}` : ""}`, { scroll: false });
    }
    // searchParams в зависимостях оставим — URL внешне может измениться
    // (например, back-навигация), и нам надо синхронизироваться.
  }, [debouncedQ, phonetic, debouncedMin, debouncedMax, offset, router, searchParams, treeId]);

  const query = useQuery({
    queryKey: [
      "persons-search",
      treeId,
      { q: debouncedQ, phonetic, birthYearMin: minYear, birthYearMax: maxYear, offset },
    ],
    queryFn: () =>
      searchPersons(treeId, {
        q: debouncedQ || undefined,
        phonetic: phonetic || undefined,
        birthYearMin: minYear,
        birthYearMax: maxYear,
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
  const hasFilters = Boolean(debouncedQ || debouncedMin || debouncedMax || phonetic);

  // Phase 4.9: pending-hypotheses count для бейджа в шапке. Лёгкий запрос
  // (`limit=1`) — нам нужно только `total` поле; ряды не материализуем.
  const pendingQuery = useQuery({
    queryKey: ["hypotheses-pending-count", treeId],
    queryFn: () =>
      fetchHypotheses(treeId, {
        reviewStatus: "pending",
        minConfidence: 0,
        limit: 1,
        offset: 0,
      }),
    enabled: Boolean(treeId),
    // Считаем счётчик минутами — обновляется при каждом возврате на страницу.
    staleTime: 60_000,
  });
  const pendingCount = pendingQuery.data?.total ?? 0;

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

  const clearFilters = () => {
    setQ("");
    setBirthYearMin("");
    setBirthYearMax("");
    setPhonetic(false);
    router.replace(`/trees/${treeId}/persons`, { scroll: false });
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
        <div className="flex flex-wrap items-center gap-2">
          {/* Phase 4.9: pending-hypotheses бейдж + ссылка в queue. Запрос
              лёгкий (limit=1, считаем total), staleTime=60s. */}
          {pendingCount > 0 ? (
            <Button variant="secondary" size="md" asChild>
              <Link href={`/trees/${treeId}/hypotheses`}>
                {pendingCount.toLocaleString("en-US")} pending hypotheses →
              </Link>
            </Button>
          ) : (
            <Button variant="ghost" size="md" asChild>
              <Link href={`/trees/${treeId}/hypotheses`}>Hypotheses</Link>
            </Button>
          )}
          {/* Без pending-count для duplicates: dedup-scoring проходит по
              всему дереву и при 61k персон занимает секунды. Запускать
              на каждый рендер списка персон — регрессия. Точное число
              пар видно на самой странице duplicates после slider'а. */}
          <Button variant="secondary" size="md" asChild>
            <Link href={`/trees/${treeId}/duplicates`}>Review duplicates →</Link>
          </Button>
        </div>
      </header>

      <section
        aria-label="Search filters"
        className="mb-6 flex flex-col gap-3 rounded-lg ring-1 ring-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 sm:flex-row sm:items-end"
      >
        <div className="flex flex-1 flex-col gap-1.5">
          <label
            htmlFor="persons-search-name"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Name
          </label>
          <Input
            id="persons-search-name"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. Zhitnitzky"
          />
        </div>
        <div className="flex w-32 flex-col gap-1.5">
          <label
            htmlFor="persons-search-min-year"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Born ≥
          </label>
          <Input
            id="persons-search-min-year"
            type="number"
            inputMode="numeric"
            min={1}
            max={9999}
            value={birthYearMin}
            onChange={(e) => setBirthYearMin(e.target.value)}
            placeholder="1850"
          />
        </div>
        <div className="flex w-32 flex-col gap-1.5">
          <label
            htmlFor="persons-search-max-year"
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
          >
            Born ≤
          </label>
          <Input
            id="persons-search-max-year"
            type="number"
            inputMode="numeric"
            min={1}
            max={9999}
            value={birthYearMax}
            onChange={(e) => setBirthYearMax(e.target.value)}
            placeholder="1900"
          />
        </div>
        <Button
          variant="ghost"
          size="md"
          onClick={clearFilters}
          disabled={!hasFilters}
          aria-label="Clear all filters"
        >
          Clear
        </Button>
      </section>

      {/* Phonetic toggle — отдельной строкой под фильтрами, потому что
          это behaviour-modifier, а не сам фильтр. Tooltip через native
          <abbr title> чтобы не тянуть Radix Tooltip ради одного MVP-toggle. */}
      <div className="-mt-3 mb-6 flex items-center gap-2 text-sm text-[color:var(--color-ink-700)]">
        <Checkbox
          id="persons-search-phonetic"
          checked={phonetic}
          onChange={(e) => setPhonetic(e.target.checked)}
        />
        <label htmlFor="persons-search-phonetic" className="cursor-pointer">
          Phonetic search{" "}
          <abbr
            title="Daitch-Mokotoff: find name variants across spellings — Zhitnitzky finds Жытницкий, Zhytnicki, Schitnitzky, etc. Useful for Jewish / Eastern European genealogy."
            className="cursor-help text-[color:var(--color-ink-500)] no-underline"
          >
            (Daitch-Mokotoff)
          </abbr>
        </label>
      </div>

      {query.isLoading ? <PersonsListSkeleton /> : null}

      {query.isError ? (
        <PersonsListError error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {data && data.items.length === 0 ? (
        <PersonsEmptyState query={debouncedQ} hasFilters={hasFilters} onClear={clearFilters} />
      ) : null}

      {data && data.items.length > 0 ? (
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
                  {person.match_type === "phonetic" ? (
                    <span className="ml-2 text-xs italic text-[color:var(--color-ink-500)]">
                      via phonetic match
                    </span>
                  ) : null}
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

function PersonsEmptyState({
  query,
  hasFilters,
  onClear,
}: {
  query: string;
  hasFilters: boolean;
  onClear: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {query ? <>Nothing found for &ldquo;{query}&rdquo;</> : "No persons match these filters"}
        </CardTitle>
        <CardDescription>
          Try a shorter substring, broaden the year range, or clear filters to see everyone.
        </CardDescription>
      </CardHeader>
      {hasFilters ? (
        <CardContent>
          <Button variant="primary" size="sm" onClick={onClear}>
            Clear filters
          </Button>
        </CardContent>
      ) : null}
    </Card>
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
    <Card className="border-red-200 ring-red-200">
      <CardHeader>
        <CardTitle>Search failed</CardTitle>
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
