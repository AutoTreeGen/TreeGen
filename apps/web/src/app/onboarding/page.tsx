"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useReducer } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  INITIAL_ONBOARDING_STATE,
  type OnboardingSource,
  onboardingReducer,
} from "@/lib/onboarding-machine";

/**
 * Phase 4.12 — first-time onboarding wizard (3 steps).
 *
 * Step 1 — выбор источника (GEDCOM / FamilySearch / blank).
 * Step 2 — собственно импорт; deep-link'и в существующие flow:
 *           - GEDCOM   → /trees/{newTreeId}/import (Phase 3.5)
 *           - FamilySearch → /familysearch/connect (Phase 5.1)
 *           - blank    → создаём пустое дерево (TODO: Phase 4.13 wires
 *                        backend POST /trees; здесь — placeholder).
 * Step 3 — done; CTA в dashboard / открыть дерево.
 *
 * State machine — в `lib/onboarding-machine.ts`, тестируется отдельно.
 */
export default function OnboardingPage() {
  const t = useTranslations("onboarding");
  const [state, dispatch] = useReducer(onboardingReducer, INITIAL_ONBOARDING_STATE);

  const stepIdx = state.step === "choose-source" ? 1 : state.step === "import" ? 2 : 3;

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <header className="mb-8 text-center">
        <h1 className="text-balance text-3xl font-bold tracking-tight md:text-4xl">{t("title")}</h1>
        <p className="mt-2 text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
        <p className="mt-4 text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
          {t("stepLabel", { current: stepIdx, total: 3 })}
        </p>
      </header>

      {state.step === "choose-source" ? (
        <ChooseSource
          onPick={(source) => dispatch({ type: "pick-source", source })}
          labels={{
            title: t("step1.title"),
            subtitle: t("step1.subtitle"),
            gedcom: t("step1.gedcom"),
            gedcomDesc: t("step1.gedcomDesc"),
            familysearch: t("step1.familysearch"),
            familysearchDesc: t("step1.familysearchDesc"),
            blank: t("step1.blank"),
            blankDesc: t("step1.blankDesc"),
            next: t("step1.next"),
          }}
        />
      ) : null}

      {state.step === "import" ? (
        <ImportStep
          source={state.source}
          treeName={state.treeName}
          onTreeNameChange={(name) => dispatch({ type: "set-tree-name", name })}
          onBack={() => dispatch({ type: "back" })}
          onSubmit={() => dispatch({ type: "submit-import" })}
          labels={{
            title: t("step2.title"),
            subtitleGedcom: t("step2.subtitleGedcom"),
            subtitleFamilysearch: t("step2.subtitleFamilysearch"),
            subtitleBlank: t("step2.subtitleBlank"),
            treeNameLabel: t("step2.treeNameLabel"),
            treeNamePlaceholder: t("step2.treeNamePlaceholder"),
            fileLabel: t("step2.fileLabel"),
            back: t("step2.back"),
            continue: t("step2.continue"),
            openFamilySearch: t("step2.openFamilySearch"),
          }}
        />
      ) : null}

      {state.step === "done" ? (
        <DoneStep
          treeName={state.treeName || "tree"}
          labels={{
            title: t("step3.title"),
            subtitle: t("step3.subtitle"),
            openTree: t("step3.openTree"),
            dashboard: t("step3.dashboard"),
          }}
        />
      ) : null}
    </main>
  );
}

function ChooseSource({
  onPick,
  labels,
}: {
  onPick: (source: OnboardingSource) => void;
  labels: {
    title: string;
    subtitle: string;
    gedcom: string;
    gedcomDesc: string;
    familysearch: string;
    familysearchDesc: string;
    blank: string;
    blankDesc: string;
    next: string;
  };
}) {
  return (
    <section aria-labelledby="step1-heading" className="space-y-4">
      <header>
        <h2 id="step1-heading" className="text-xl font-semibold">
          {labels.title}
        </h2>
        <p className="text-sm text-[color:var(--color-ink-500)]">{labels.subtitle}</p>
      </header>
      <div className="grid gap-3">
        <SourceOption
          label={labels.gedcom}
          description={labels.gedcomDesc}
          onClick={() => onPick("gedcom")}
          testId="onboarding-source-gedcom"
        />
        <SourceOption
          label={labels.familysearch}
          description={labels.familysearchDesc}
          onClick={() => onPick("familysearch")}
          testId="onboarding-source-familysearch"
        />
        <SourceOption
          label={labels.blank}
          description={labels.blankDesc}
          onClick={() => onPick("blank")}
          testId="onboarding-source-blank"
        />
      </div>
    </section>
  );
}

function SourceOption({
  label,
  description,
  onClick,
  testId,
}: {
  label: string;
  description: string;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 text-left transition hover:border-[color:var(--color-accent)] hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
    >
      <div className="font-medium">{label}</div>
      <div className="mt-1 text-sm text-[color:var(--color-ink-500)]">{description}</div>
    </button>
  );
}

function ImportStep({
  source,
  treeName,
  onTreeNameChange,
  onBack,
  onSubmit,
  labels,
}: {
  source: OnboardingSource;
  treeName: string;
  onTreeNameChange: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
  labels: {
    title: string;
    subtitleGedcom: string;
    subtitleFamilysearch: string;
    subtitleBlank: string;
    treeNameLabel: string;
    treeNamePlaceholder: string;
    fileLabel: string;
    back: string;
    continue: string;
    openFamilySearch: string;
  };
}) {
  const subtitle =
    source === "gedcom"
      ? labels.subtitleGedcom
      : source === "familysearch"
        ? labels.subtitleFamilysearch
        : labels.subtitleBlank;

  return (
    <section aria-labelledby="step2-heading" className="space-y-4">
      <header>
        <h2 id="step2-heading" className="text-xl font-semibold">
          {labels.title}
        </h2>
        <p className="text-sm text-[color:var(--color-ink-500)]">{subtitle}</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base capitalize">{source}</CardTitle>
          <CardDescription>{subtitle}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label htmlFor="onboarding-tree-name" className="flex flex-col gap-1 text-xs">
            <span className="uppercase tracking-wide text-[color:var(--color-ink-500)]">
              {labels.treeNameLabel}
            </span>
            <Input
              id="onboarding-tree-name"
              type="text"
              value={treeName}
              onChange={(e) => onTreeNameChange(e.target.value)}
              placeholder={labels.treeNamePlaceholder}
              required
            />
          </label>

          {source === "gedcom" ? (
            <label htmlFor="onboarding-gedcom-file" className="flex flex-col gap-1 text-xs">
              <span className="uppercase tracking-wide text-[color:var(--color-ink-500)]">
                {labels.fileLabel}
              </span>
              <Input id="onboarding-gedcom-file" type="file" accept=".ged,.gedcom" />
            </label>
          ) : null}

          {source === "familysearch" ? (
            <Button variant="primary" size="md" asChild>
              <Link href="/familysearch/connect">{labels.openFamilySearch}</Link>
            </Button>
          ) : null}
        </CardContent>
      </Card>

      <div className="flex justify-between gap-2">
        <Button variant="ghost" size="md" onClick={onBack}>
          ← {labels.back}
        </Button>
        <Button
          variant="primary"
          size="md"
          onClick={onSubmit}
          disabled={treeName.trim().length === 0}
          data-testid="onboarding-continue"
        >
          {labels.continue}
        </Button>
      </div>
    </section>
  );
}

function DoneStep({
  treeName,
  labels,
}: {
  treeName: string;
  labels: { title: string; subtitle: string; openTree: string; dashboard: string };
}) {
  return (
    <section aria-labelledby="step3-heading" className="space-y-4 text-center">
      <header>
        <h2 id="step3-heading" className="text-2xl font-semibold">
          {labels.title}
        </h2>
        <p className="mt-2 text-[color:var(--color-ink-500)]">{labels.subtitle}</p>
        <p className="mt-3 inline-block rounded-full bg-emerald-100 px-3 py-1 text-xs text-emerald-900">
          {treeName}
        </p>
      </header>
      <div className="flex flex-wrap justify-center gap-2">
        <Button variant="primary" size="md" asChild>
          {/* TODO Phase 4.13: подменить на /trees/{newId} как только backend
              POST /trees вернёт реальный id из onboarding submit. */}
          <Link href="/dashboard">{labels.openTree}</Link>
        </Button>
        <Button variant="secondary" size="md" asChild>
          <Link href="/dashboard">{labels.dashboard}</Link>
        </Button>
      </div>
    </section>
  );
}
