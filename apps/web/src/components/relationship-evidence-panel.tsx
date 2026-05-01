"use client";

/**
 * Phase 15.1 — RelationshipEvidencePanel (см. ADR-0058).
 *
 * Right-side drawer над tree-view, показывает sources / inference rules
 * supporting / contradicting конкретную relationship (parent_child / spouse /
 * sibling). Tabs: Supporting (default) | Contradicting | Provenance.
 *
 * Анти-drift Phase 15.1:
 * - "Add evidence" — disabled placeholder (Phase 15.2).
 * - "Add archive search" — disabled placeholder (Phase 15.5).
 * - Tree rendering / D3 / canvas — не трогаем; trigger ожидается извне.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type RelationshipEvidenceConfidence,
  type RelationshipEvidenceProvenance,
  type RelationshipEvidenceResponse,
  type RelationshipEvidenceSource,
  type RelationshipKind,
  fetchRelationshipEvidence,
} from "@/lib/relationships-api";
import { cn } from "@/lib/utils";

export type RelationshipEvidencePanelProps = {
  /** Открыта ли панель. Внешний state (parent owns toggle). */
  open: boolean;
  /** Закрыть панель (X-кнопка / overlay-click). */
  onClose: () => void;
  treeId: string;
  kind: RelationshipKind;
  subjectId: string;
  objectId: string;
  /** Display label для subject person (UI рендерит "<A> ↔ <B>"). */
  subjectLabel?: string;
  objectLabel?: string;
};

type TabKey = "supporting" | "contradicting" | "provenance";

export function RelationshipEvidencePanel({
  open,
  onClose,
  treeId,
  kind,
  subjectId,
  objectId,
  subjectLabel,
  objectLabel,
}: RelationshipEvidencePanelProps) {
  const t = useTranslations("relationshipEvidence");
  const [activeTab, setActiveTab] = useState<TabKey>("supporting");

  const query = useQuery({
    queryKey: ["relationship-evidence", treeId, kind, subjectId, objectId],
    queryFn: () => fetchRelationshipEvidence({ treeId, kind, subjectId, objectId }),
    enabled: open,
    staleTime: 60_000,
  });

  if (!open) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        aria-label={t("close")}
        className="fixed inset-0 z-40 bg-black/30"
        onClick={onClose}
      />
      <aside
        aria-modal="true"
        aria-label={t("ariaLabel")}
        data-testid="relationship-evidence-panel"
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl"
      >
        <PanelHeader
          subjectLabel={subjectLabel ?? subjectId}
          objectLabel={objectLabel ?? objectId}
          kind={kind}
          confidence={query.data?.confidence}
          onClose={onClose}
        />
        <PanelTabs activeTab={activeTab} onChange={setActiveTab} />
        <div className="flex-1 overflow-y-auto p-4">
          {query.isLoading ? (
            <LoadingSkeleton />
          ) : query.isError ? (
            <ErrorState message={String(query.error)} />
          ) : query.data ? (
            <PanelBody activeTab={activeTab} data={query.data} />
          ) : null}
        </div>
        <PanelFooter />
      </aside>
    </>
  );
}

// ---- Header ---------------------------------------------------------------

function PanelHeader({
  subjectLabel,
  objectLabel,
  kind,
  confidence,
  onClose,
}: {
  subjectLabel: string;
  objectLabel: string;
  kind: RelationshipKind;
  confidence: RelationshipEvidenceConfidence | undefined;
  onClose: () => void;
}) {
  const t = useTranslations("relationshipEvidence");
  return (
    <div className="flex items-start justify-between border-b p-4">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold">{t("headerTitle", { kind: t(`kind.${kind}`) })}</h2>
        <p className="text-sm text-muted-foreground">
          <span data-testid="evidence-subject-label">{subjectLabel}</span>
          <span aria-hidden="true" className="px-2">
            ↔
          </span>
          <span data-testid="evidence-object-label">{objectLabel}</span>
        </p>
        {confidence ? <ConfidenceBadge confidence={confidence} /> : null}
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={onClose}
        aria-label={t("close")}
        data-testid="evidence-close-button"
      >
        ×
      </Button>
    </div>
  );
}

// ---- Confidence badge ------------------------------------------------------

/**
 * Цветовые границы согласованы с UI requirement из ADR-0058:
 * green ≥ 0.85, amber 0.6..0.85, red < 0.6. Method='naive_count' дополнительно
 * мажется муtedным префиксом — пользователь должен видеть «не настоящий
 * Bayesian rollup, naive count over citations».
 */
export function confidenceBadgeTone(score: number): "green" | "amber" | "red" {
  if (score >= 0.85) return "green";
  if (score >= 0.6) return "amber";
  return "red";
}

function ConfidenceBadge({
  confidence,
}: {
  confidence: RelationshipEvidenceConfidence;
}) {
  const t = useTranslations("relationshipEvidence");
  const tone = confidenceBadgeTone(confidence.score);
  const toneClasses: Record<typeof tone, string> = {
    green: "bg-green-100 text-green-900 border-green-300",
    amber: "bg-amber-100 text-amber-900 border-amber-300",
    red: "bg-red-100 text-red-900 border-red-300",
  };
  return (
    <Badge
      data-testid="confidence-badge"
      data-tone={tone}
      data-method={confidence.method}
      className={cn("border", toneClasses[tone])}
    >
      {t(`confidenceMethod.${confidence.method}`)}: {confidence.score.toFixed(2)}
    </Badge>
  );
}

// ---- Tabs -----------------------------------------------------------------

function PanelTabs({
  activeTab,
  onChange,
}: {
  activeTab: TabKey;
  onChange: (tab: TabKey) => void;
}) {
  const t = useTranslations("relationshipEvidence");
  const tabs: { key: TabKey; label: string }[] = [
    { key: "supporting", label: t("tabs.supporting") },
    { key: "contradicting", label: t("tabs.contradicting") },
    { key: "provenance", label: t("tabs.provenance") },
  ];
  return (
    <div role="tablist" className="flex border-b">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.key}
          aria-controls={`evidence-tab-${tab.key}`}
          data-testid={`evidence-tab-${tab.key}`}
          onClick={() => onChange(tab.key)}
          className={cn(
            "flex-1 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            activeTab === tab.key
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ---- Body -----------------------------------------------------------------

function PanelBody({
  activeTab,
  data,
}: {
  activeTab: TabKey;
  data: RelationshipEvidenceResponse;
}) {
  if (activeTab === "supporting") {
    return (
      <SourceList
        items={data.supporting}
        emptyState={<EmptySupporting />}
        testIdPrefix="supporting"
      />
    );
  }
  if (activeTab === "contradicting") {
    return (
      <SourceList
        items={data.contradicting}
        emptyState={<EmptyContradicting />}
        testIdPrefix="contradicting"
      />
    );
  }
  return <ProvenanceTab provenance={data.provenance} />;
}

function SourceList({
  items,
  emptyState,
  testIdPrefix,
}: {
  items: RelationshipEvidenceSource[];
  emptyState: React.ReactNode;
  testIdPrefix: string;
}) {
  if (items.length === 0) {
    return <>{emptyState}</>;
  }
  return (
    <ul className="space-y-3">
      {items.map((item, idx) => (
        <SourceCard
          key={`${item.source_id ?? item.rule_id ?? "ev"}-${idx}`}
          source={item}
          testId={`${testIdPrefix}-source-${idx}`}
        />
      ))}
    </ul>
  );
}

function SourceCard({
  source,
  testId,
}: {
  source: RelationshipEvidenceSource;
  testId: string;
}) {
  const t = useTranslations("relationshipEvidence");
  return (
    <li data-testid={testId} data-source-kind={source.kind} className="rounded-md border p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{source.title}</h3>
        {source.reliability !== null ? (
          <span className="text-xs text-muted-foreground" data-testid={`${testId}-reliability`}>
            {t("reliability", { value: source.reliability.toFixed(2) })}
          </span>
        ) : null}
      </div>
      {source.repository ? (
        <p className="text-xs text-muted-foreground">{source.repository}</p>
      ) : null}
      {source.citation ? (
        <p className="mt-1 text-xs italic text-muted-foreground">{source.citation}</p>
      ) : null}
      {source.snippet ? <p className="mt-2 text-sm">"{source.snippet}"</p> : null}
      {source.url ? (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-xs text-primary underline"
        >
          {t("openSource")}
        </a>
      ) : null}
    </li>
  );
}

// ---- Provenance tab -------------------------------------------------------

function ProvenanceTab({
  provenance,
}: {
  provenance: RelationshipEvidenceProvenance;
}) {
  const t = useTranslations("relationshipEvidence");
  const empty =
    provenance.source_files.length === 0 &&
    provenance.import_job_id === null &&
    provenance.manual_edits.length === 0;
  if (empty) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="provenance-empty">
        {t("emptyProvenance")}
      </p>
    );
  }
  return (
    <dl className="space-y-3 text-sm" data-testid="provenance-content">
      {provenance.source_files.length > 0 ? (
        <div>
          <dt className="font-medium">{t("provenance.sourceFiles")}</dt>
          <dd>
            <ul className="ml-4 list-disc text-muted-foreground">
              {provenance.source_files.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </dd>
        </div>
      ) : null}
      {provenance.import_job_id !== null ? (
        <div>
          <dt className="font-medium">{t("provenance.importJob")}</dt>
          <dd className="font-mono text-xs text-muted-foreground">{provenance.import_job_id}</dd>
        </div>
      ) : null}
      {provenance.manual_edits.length > 0 ? (
        <div>
          <dt className="font-medium">{t("provenance.manualEdits")}</dt>
          <dd className="text-muted-foreground">
            {t("provenance.manualEditsCount", {
              count: provenance.manual_edits.length,
            })}
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

// ---- Empty / loading / error ----------------------------------------------

function EmptySupporting() {
  const t = useTranslations("relationshipEvidence");
  return (
    <div
      data-testid="empty-supporting"
      className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
    >
      <p className="font-medium">{t("emptySupporting.title")}</p>
      <p className="mt-1 text-xs">{t("emptySupporting.description")}</p>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        disabled
        data-testid="add-archive-search-cta"
      >
        {t("emptySupporting.cta")}
      </Button>
    </div>
  );
}

function EmptyContradicting() {
  const t = useTranslations("relationshipEvidence");
  return (
    <p data-testid="empty-contradicting" className="text-sm text-muted-foreground">
      {t("emptyContradicting")}
    </p>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3" data-testid="evidence-loading">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  const t = useTranslations("relationshipEvidence");
  return (
    <p
      data-testid="evidence-error"
      className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900"
    >
      {t("error", { message })}
    </p>
  );
}

// ---- Footer ---------------------------------------------------------------

function PanelFooter() {
  const t = useTranslations("relationshipEvidence");
  return (
    <div className="border-t p-3">
      <Button
        variant="primary"
        size="sm"
        disabled
        className="w-full"
        data-testid="add-evidence-cta"
      >
        {t("addEvidence")}
      </Button>
      <p className="mt-1 text-center text-[11px] text-muted-foreground">{t("addEvidenceHint")}</p>
    </div>
  );
}
