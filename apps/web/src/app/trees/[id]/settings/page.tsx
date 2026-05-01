"use client";

/**
 * /trees/[id]/settings — Phase 10.7a / ADR-0068.
 *
 * Поверхность для tree-уровневых настроек. На V1 содержит только
 * SetEgoPersonPicker (self-anchor). Phase 10.9b+ присоединит сюда
 * voice-consent toggle и прочее. До тех пор страница single-section.
 *
 * Permission gating: страница рендерится с любой ролью; внутри picker
 * сам проверяет ``canEdit`` и рисует disabled-state для non-owner.
 */

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { SetEgoPersonPicker } from "@/components/set-ego-person-picker";
import { Button } from "@/components/ui/button";
import { ApiError, fetchMembers, fetchTreeOwnerPerson } from "@/lib/api";
import { fetchMe } from "@/lib/user-settings-api";

export default function TreeSettingsPage() {
  return (
    <Suspense fallback={null}>
      <TreeSettingsPageContent />
    </Suspense>
  );
}

function TreeSettingsPageContent() {
  const t = useTranslations("trees.settings");
  const params = useParams<{ id: string }>();
  const treeId = params.id;
  const [ownerPersonId, setOwnerPersonId] = useState<string | null>(null);

  const me = useQuery({ queryKey: ["me"], queryFn: fetchMe, refetchOnWindowFocus: false });
  const members = useQuery({
    queryKey: ["members", treeId],
    queryFn: () => fetchMembers(treeId),
    refetchOnWindowFocus: false,
  });

  // Initial owner_person_id грузим через GET /trees/{id}/owner-person.
  useEffect(() => {
    if (!treeId) return;
    let cancelled = false;
    void fetchTreeOwnerPerson(treeId)
      .then((data) => {
        if (!cancelled) setOwnerPersonId(data.owner_person_id);
      })
      .catch(() => {
        // Tree не существует / 403 — оставляем null. SetEgoPersonPicker
        // покажет «not anchored» state.
      });
    return () => {
      cancelled = true;
    };
  }, [treeId]);

  const owner = members.data?.items.find((m) => m.role === "owner");
  const canEdit = me.data !== undefined && owner !== undefined && me.data.id === owner.user_id;

  if (members.isError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
        <p className="text-sm text-red-800" role="alert">
          {members.error instanceof ApiError ? members.error.message : t("loadError")}
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
      <header className="mb-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← {t("backToTree")}</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
      </header>

      <div className="space-y-6">
        <SetEgoPersonPicker
          treeId={treeId}
          currentOwnerPersonId={ownerPersonId}
          canEdit={canEdit}
          onChange={(response) => setOwnerPersonId(response.owner_person_id)}
        />
      </div>
    </main>
  );
}
