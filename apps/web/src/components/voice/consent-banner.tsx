"use client";

/**
 * Phase 10.9d — Consent banner для voice egress.
 *
 * Owner кликает "I consent" → POST /trees/{id}/audio-consent. До этого
 * Recorder disabled, backend возвращает 403 ``consent_required``.
 *
 * Owner-only revoke: для не-owner'а кнопка hidden + hint строкой. Owner-
 * проверка на UI — soft (UX-смысл); реальная защита — RBAC gate в
 * backend'е (require_tree_role(OWNER)).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type AudioConsentResponse,
  fetchAudioConsent,
  grantAudioConsent,
  revokeAudioConsent,
} from "@/lib/voice-api";

export type ConsentBannerProps = {
  treeId: string;
  /**
   * Может ли текущий пользователь менять состояние consent'а. Только
   * owner — по контракту backend'а (audio_consent.py). Если false —
   * показываем только текущее состояние, без кнопок.
   */
  canManageConsent: boolean;
  /**
   * Hook для тестов и для page-уровневых эффектов: вызывается каждый
   * раз когда consent изменился (granted/revoked). Page может на это
   * реагировать — например, инвалидировать sessions list после revoke.
   */
  onConsentChange?: (state: AudioConsentResponse) => void;
};

export function ConsentBanner({ treeId, canManageConsent, onConsentChange }: ConsentBannerProps) {
  const t = useTranslations("voice.consent");
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const consent = useQuery({
    queryKey: ["audio-consent", treeId],
    queryFn: () => fetchAudioConsent(treeId),
    refetchOnWindowFocus: false,
  });

  const grant = useMutation({
    mutationFn: () => grantAudioConsent(treeId),
    onSuccess: (data) => {
      setError(null);
      queryClient.setQueryData(["audio-consent", treeId], data);
      onConsentChange?.(data);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : t("grantFailed"));
    },
  });

  const revoke = useMutation({
    mutationFn: () => revokeAudioConsent(treeId),
    onSuccess: (data) => {
      setError(null);
      // После revoke endpoint возвращает RevokeResponse (без consent fields);
      // обновляем cache на «consent отозван» вручную, так как это shape отличается.
      const cleared: AudioConsentResponse = {
        tree_id: data.tree_id,
        audio_consent_egress_at: null,
        audio_consent_egress_provider: null,
      };
      queryClient.setQueryData(["audio-consent", treeId], cleared);
      onConsentChange?.(cleared);
      // Erasure-job'ы поставлены, sessions list скоро опустеет — invalidate
      // чтобы UI отразил.
      void queryClient.invalidateQueries({ queryKey: ["audio-sessions", treeId] });
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : t("revokeFailed"));
    },
  });

  const onRevokeClick = () => {
    if (typeof window === "undefined" || window.confirm(t("revokeConfirm"))) {
      revoke.mutate();
    }
  };

  if (consent.isLoading) {
    return null;
  }

  if (consent.isError) {
    return (
      <Card data-testid="consent-banner">
        <CardHeader>
          <CardTitle>{t("heading")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-red-800" role="alert">
            {t("loadFailed")}
          </p>
        </CardContent>
      </Card>
    );
  }

  const granted = consent.data?.audio_consent_egress_at ?? null;
  const grantedDate = granted ? new Date(granted).toLocaleDateString() : null;

  return (
    <Card data-testid="consent-banner">
      <CardHeader>
        <CardTitle>{t("heading")}</CardTitle>
        {!granted ? <CardDescription>{t("body")}</CardDescription> : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {granted ? (
          <p className="text-sm text-[color:var(--color-ink-700)]">
            {t("grantedAt", { date: grantedDate ?? "" })}
          </p>
        ) : null}

        {!canManageConsent ? (
          <p className="text-xs text-[color:var(--color-ink-500)]">{t("ownerOnlyHint")}</p>
        ) : granted ? (
          <Button
            type="button"
            variant="secondary"
            size="md"
            onClick={onRevokeClick}
            disabled={revoke.isPending}
            data-testid="consent-revoke"
          >
            {revoke.isPending ? t("revoking") : t("revoke")}
          </Button>
        ) : (
          <Button
            type="button"
            variant="primary"
            size="md"
            onClick={() => grant.mutate()}
            disabled={grant.isPending}
            data-testid="consent-grant"
          >
            {grant.isPending ? t("granting") : t("grant")}
          </Button>
        )}

        {error ? (
          <p className="text-sm text-red-800" role="alert">
            {error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
