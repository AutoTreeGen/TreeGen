"use client";

/**
 * /familysearch/preview/[fsPersonId] — preview pedigree до импорта (Phase 5.1).
 *
 * Динамический сегмент — FamilySearch person id (focus persona). Tree id
 * выбирается через query-param ``?tree=...`` или select-input на странице.
 * После Confirm → POST /imports/familysearch/import → редирект на
 * /familysearch/import/[importJobId].
 *
 * Brief упоминал ``[importJobId]`` для preview-роута — это семантически
 * не подходит (preview не создаёт job). Используем ``[fsPersonId]``;
 * /familysearch/import/[importJobId] следует тому же паттерну.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { type ChangeEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, fetchFamilySearchPreview, startFamilySearchImport } from "@/lib/api";

const DEFAULT_GENERATIONS = 4;

export default function FamilySearchPreviewPage() {
  const t = useTranslations("familysearch.preview");
  const params = useParams<{ fsPersonId: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const fsPersonId = decodeURIComponent(params.fsPersonId ?? "");

  const initialTreeId = search.get("tree") ?? "";
  const [treeId, setTreeId] = useState(initialTreeId);
  const [generations, setGenerations] = useState(DEFAULT_GENERATIONS);

  const preview = useQuery({
    queryKey: ["fs-preview", fsPersonId, generations],
    queryFn: () => fetchFamilySearchPreview(fsPersonId, generations),
    enabled: Boolean(fsPersonId),
    refetchOnWindowFocus: false,
  });

  const importMutation = useMutation({
    mutationFn: () =>
      startFamilySearchImport({
        fs_person_id: fsPersonId,
        tree_id: treeId,
        generations,
      }),
    onSuccess: (job) => {
      router.push(`/familysearch/import/${job.id}`);
    },
  });

  const canConfirm = Boolean(treeId) && preview.isSuccess && !importMutation.isPending;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/familysearch/connect">← Back to FamilySearch</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          We&apos;ll fetch your ancestors from FamilySearch read-only first. Nothing is written to
          your local tree until you click Confirm.
        </p>
      </header>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>{t("importSettings")}</CardTitle>
          <CardDescription>
            Focus persona <span className="font-mono">{fsPersonId || "—"}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Tree ID">
            <Input
              value={treeId}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setTreeId(e.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
            />
          </Field>
          <Field label="Generations (1–8)">
            <Input
              type="number"
              min={1}
              max={8}
              value={generations}
              onChange={(e: ChangeEvent<HTMLInputElement>) => {
                const next = Number.parseInt(e.target.value, 10);
                if (!Number.isNaN(next)) setGenerations(Math.min(8, Math.max(1, next)));
              }}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("pedigreeSummary")}</CardTitle>
          <CardDescription>
            {preview.isLoading
              ? "Fetching from FamilySearch…"
              : preview.isError
                ? "Couldn’t fetch preview."
                : preview.data
                  ? `${preview.data.person_count.toLocaleString("en-US")} unique ancestors across ${preview.data.generations} generations.`
                  : "No data."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {preview.isError ? <PreviewError error={preview.error} /> : null}

          {preview.data ? (
            <ul className="flex flex-col divide-y divide-[color:var(--color-border)]">
              {preview.data.sample_persons.map((p) => (
                <li key={p.fs_person_id} className="flex items-center justify-between py-2">
                  <div>
                    <p className="text-sm font-medium">{p.primary_name ?? "(unnamed)"}</p>
                    <p className="text-xs text-[color:var(--color-ink-500)]">
                      {p.lifespan ?? "no dates"}
                    </p>
                  </div>
                  <span className="font-mono text-xs text-[color:var(--color-ink-500)]">
                    {p.fs_person_id}
                  </span>
                </li>
              ))}
              {preview.data.person_count > preview.data.sample_persons.length ? (
                <li className="py-2 text-xs text-[color:var(--color-ink-500)]">
                  …and{" "}
                  {(preview.data.person_count - preview.data.sample_persons.length).toLocaleString(
                    "en-US",
                  )}{" "}
                  more.
                </li>
              ) : null}
            </ul>
          ) : null}

          {importMutation.isError ? <PreviewError error={importMutation.error} /> : null}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="md"
              disabled={!canConfirm}
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending ? "Starting import…" : "Confirm and import"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  // <fieldset>+<legend> — нативный аналог role=group, biome lint разрешает.
  // Используем такой паттерн потому что внутри ``children`` живёт наш
  // Input-компонент, а biome не трассирует props через React-абстракцию
  // и ругается на «label без явного control» при <label>{children}</label>.
  return (
    <fieldset className="flex flex-col gap-1.5 border-0 p-0">
      <legend className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
        {label}
      </legend>
      {children}
    </fieldset>
  );
}

function PreviewError({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? `${error.status}: ${error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
    >
      {message}
    </div>
  );
}
