"use client";

/**
 * /trees/[id]/voice — Phase 10.9d / ADR-0064.
 *
 * Voice-to-tree поверхность: consent banner + recorder + sessions list.
 * Read-only транскрипты — edit-mode появится в более поздней iteration.
 *
 * Permission gating: страница рендерится для любой роли (VIEWER+ через
 * RBAC backend'а). Внутри:
 *  - ConsentBanner — кнопки visible только для owner'а; viewer/editor
 *    видят текущее состояние без CTA.
 *  - Recorder — disabled для всех, пока consent отсутствует. Backend
 *    тоже запретит EDITOR'у запись без owner-consent'а.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { ConsentBanner } from "@/components/voice/consent-banner";
import { Recorder } from "@/components/voice/recorder";
import { SessionsList } from "@/components/voice/sessions-list";
import { fetchMembers } from "@/lib/api";
import { fetchMe } from "@/lib/user-settings-api";
import { fetchAudioConsent } from "@/lib/voice-api";

export default function TreeVoicePage() {
  return (
    <Suspense fallback={null}>
      <TreeVoicePageContent />
    </Suspense>
  );
}

function TreeVoicePageContent() {
  const t = useTranslations("voice");
  const params = useParams<{ id: string }>();
  const treeId = params.id;
  const queryClient = useQueryClient();

  const me = useQuery({ queryKey: ["me"], queryFn: fetchMe, refetchOnWindowFocus: false });
  const members = useQuery({
    queryKey: ["members", treeId],
    queryFn: () => fetchMembers(treeId),
    refetchOnWindowFocus: false,
    enabled: Boolean(treeId),
  });

  // Consent status тоже нужен на page-level — Recorder получает флаг
  // ``consentGranted`` от него. ConsentBanner владеет mutation'ами;
  // мы только читаем.
  const consent = useQuery({
    queryKey: ["audio-consent", treeId],
    queryFn: () => fetchAudioConsent(treeId),
    refetchOnWindowFocus: false,
    enabled: Boolean(treeId),
  });

  const owner = members.data?.items.find((m) => m.role === "owner");
  const canManageConsent =
    me.data !== undefined && owner !== undefined && me.data.id === owner.user_id;
  const consentGranted =
    consent.data?.audio_consent_egress_at !== null && consent.data !== undefined;

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10">
      <header className="mb-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/trees/${treeId}/persons`}>← {t("backToTree")}</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
        <p className="mt-2 text-xs text-[color:var(--color-ink-500)]">{t("browserSupport")}</p>
      </header>

      <div className="space-y-6">
        <ConsentBanner
          treeId={treeId}
          canManageConsent={canManageConsent}
          onConsentChange={() => {
            // Sessions list зависят от consent state'а только в части
            // отображения revoke-tombstones; invalidate, чтобы UI был свежий.
            void queryClient.invalidateQueries({ queryKey: ["audio-sessions", treeId] });
          }}
        />
        <Recorder treeId={treeId} consentGranted={consentGranted} />
        <SessionsList treeId={treeId} />
      </div>
    </main>
  );
}
