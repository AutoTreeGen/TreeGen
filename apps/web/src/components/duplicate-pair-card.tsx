import Link from "next/link";
import { Fragment } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { DuplicateEntityType, DuplicateSuggestion } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Карточка одной пары дубликатов с side-by-side сравнением полей и
 * action-кнопками. Phase 4.6: «Mark as same» для person-пар ведёт на
 * `/persons/[id]/merge/[targetId]` (manual preview → confirm flow,
 * CLAUDE.md §5). «Not duplicate» / «Skip» по-прежнему disabled —
 * rejected-pairs store будет в Phase 4.6.x.
 *
 * Evidence уже денормализован на бэкенде (`a_*` / `b_*`), поэтому
 * дополнительных fetch'ей не нужно — отрисовываем напрямую.
 */
export function DuplicatePairCard({ pair }: { pair: DuplicateSuggestion }) {
  const sideA = formatEvidenceSide(pair.entity_type, pair.evidence, "a");
  const sideB = formatEvidenceSide(pair.entity_type, pair.evidence, "b");
  const components = Object.entries(pair.components).sort(([, a], [, b]) => b - a);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="capitalize">{pair.entity_type} duplicate candidate</CardTitle>
          <ConfidenceBadge value={pair.confidence} />
        </div>
        <CardDescription className="font-mono text-xs">
          {pair.entity_a_id.slice(0, 8)}… ↔ {pair.entity_b_id.slice(0, 8)}…
        </CardDescription>
      </CardHeader>

      <CardContent>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <EntitySide title="A" data={sideA} />
          <EntitySide title="B" data={sideB} />
        </div>
      </CardContent>

      {components.length > 0 ? (
        <>
          <Separator />
          <CardContent>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[color:var(--color-ink-500)]">
              Score breakdown
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {components.map(([key, score]) => (
                <li key={key}>
                  <Badge variant="neutral">
                    <span className="font-mono text-[11px]">
                      {key}: {score.toFixed(2)}
                    </span>
                  </Badge>
                </li>
              ))}
            </ul>
          </CardContent>
        </>
      ) : null}

      <Separator />

      <CardContent>
        <p className="mb-2 text-xs italic text-[color:var(--color-ink-500)]">
          Manual review only per CLAUDE.md §5 (no auto-merge for close kin). «Mark as same» opens
          the two-step preview → confirm flow with a 90-day undo window.
        </p>
        <div className="flex flex-wrap gap-2">
          {pair.entity_type === "person" ? (
            <Button
              variant="primary"
              size="sm"
              asChild
              aria-label="Open merge preview for this pair"
            >
              <Link href={`/persons/${pair.entity_a_id}/merge/${pair.entity_b_id}`}>
                Mark as same
              </Link>
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              disabled
              aria-label="Source/place merge — coming in a later phase"
            >
              Mark as same
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            disabled
            aria-label="Mark as not duplicate — rejected-pairs store coming in Phase 4.6.x"
          >
            Not duplicate
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled
            aria-label="Skip — passive action, no backend support needed"
          >
            Skip
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  const tone = value >= 0.95 ? "high" : value >= 0.8 ? "mid" : "low";
  const palette = {
    high: "bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300",
    mid: "bg-amber-100 text-amber-900 ring-1 ring-amber-300",
    low: "bg-[color:var(--color-surface-muted)] text-[color:var(--color-ink-700)] ring-1 ring-[color:var(--color-border)]",
  } as const;

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        palette[tone],
      )}
    >
      <span aria-hidden="true">●</span>
      {(value * 100).toFixed(0)}% confidence
    </span>
  );
}

type EntitySideData = Array<[string, string]>;

function EntitySide({ title, data }: { title: string; data: EntitySideData }) {
  return (
    <div className="rounded-md bg-[color:var(--color-surface-muted)] p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-ink-500)]">
        Entity {title}
      </p>
      {data.length === 0 ? (
        <p className="text-sm text-[color:var(--color-ink-500)]">No fields available.</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {data.map(([label, value]) => (
            <Fragment key={label}>
              <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                {label}
              </dt>
              <dd className="break-words text-[color:var(--color-ink-900)]">{value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
    </div>
  );
}

function formatEvidenceSide(
  entityType: DuplicateEntityType,
  evidence: Record<string, unknown>,
  side: "a" | "b",
): EntitySideData {
  const get = (key: string): string | null => {
    const raw = evidence[`${side}_${key}`];
    if (raw === null || raw === undefined || raw === "") return null;
    return String(raw);
  };

  switch (entityType) {
    case "person": {
      const fields: EntitySideData = [];
      const name = get("name");
      const birthYear = get("birth_year");
      const birthPlace = get("birth_place");
      if (name) fields.push(["Name", name]);
      if (birthYear) fields.push(["Birth year", birthYear]);
      if (birthPlace) fields.push(["Birth place", birthPlace]);
      return fields;
    }
    case "source": {
      const fields: EntitySideData = [];
      const title = get("title");
      const author = get("author");
      if (title) fields.push(["Title", title]);
      if (author) fields.push(["Author", author]);
      return fields;
    }
    case "place": {
      const fields: EntitySideData = [];
      const name = get("name");
      if (name) fields.push(["Canonical name", name]);
      return fields;
    }
  }
}
