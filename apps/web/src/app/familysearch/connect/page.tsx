"use client";

/**
 * /familysearch/connect — подключение FamilySearch-аккаунта (Phase 5.1).
 *
 * Один экран: кнопка «Connect FamilySearch» → редирект на FS authorize URL.
 * После того как FS вернёт user'а на ``/imports/familysearch/oauth/callback``,
 * сервер редиректит на эту же страницу с ``?status=ok|error&reason=...``.
 *
 * Phase 4.x введёт полноценный auth-middleware; пока используем
 * settings.owner_email на стороне сервера.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ApiError,
  disconnectFamilySearch,
  fetchFamilySearchAccount,
  startFamilySearchOAuth,
} from "@/lib/api";

/**
 * Человеко-понятные ярлыки для ?reason=... из failure-redirect'а
 * (см. parser_service.api.familysearch._failure_redirect).
 */
const FAILURE_REASONS: Record<string, string> = {
  declined: "You declined the FamilySearch authorization request.",
  missing_params: "FamilySearch returned an incomplete callback (missing code or state).",
  state_mismatch: "Security check failed (CSRF state mismatch). Please try again.",
  state_expired: "Authorization timed out. Please start the connection again.",
  token_exchange_failed: "FamilySearch refused to issue a token. Try connecting again.",
  upstream_error: "FamilySearch is currently unreachable. Try again in a moment.",
};

export default function FamilySearchConnectPage() {
  // Next 15 требует Suspense-boundary вокруг useSearchParams() в client
  // page'ах, иначе static export бьётся (CSR bailout). Pre-existing fix
  // вытащен сюда побочно при работе над Phase 6.3 — иначе build не проходит.
  return (
    <Suspense fallback={null}>
      <FamilySearchConnectContent />
    </Suspense>
  );
}

function FamilySearchConnectContent() {
  const t = useTranslations("familysearch.connect");
  const search = useSearchParams();
  const status = search.get("status");
  const reason = search.get("reason");
  const queryClient = useQueryClient();

  const account = useQuery({
    queryKey: ["fs-account"],
    queryFn: fetchFamilySearchAccount,
    refetchOnWindowFocus: false,
  });

  const start = useMutation({
    mutationFn: startFamilySearchOAuth,
    onSuccess: ({ authorize_url }) => {
      window.location.href = authorize_url;
    },
  });

  const disconnect = useMutation({
    mutationFn: disconnectFamilySearch,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["fs-account"] });
    },
  });

  const failureMessage = useMemo(() => {
    if (status !== "error") return null;
    if (reason && FAILURE_REASONS[reason]) return FAILURE_REASONS[reason];
    return "Something went wrong while connecting your FamilySearch account.";
  }, [status, reason]);

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <header className="mb-8">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">← Back home</Link>
        </Button>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Link your FamilySearch account to import ancestors directly into your local tree — without
          exporting/uploading GEDCOM.
        </p>
      </header>

      {status === "ok" ? (
        <output className="mb-6 block rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          FamilySearch account connected. You can now preview a pedigree.
        </output>
      ) : null}

      {failureMessage ? (
        <div
          role="alert"
          className="mb-6 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
        >
          {failureMessage}
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{t("accountStatus")}</CardTitle>
          <CardDescription>
            Tokens are encrypted at rest (Fernet) and never leave the server. See ADR-0027 for the
            full storage decision.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {account.isLoading ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">Checking connection…</p>
          ) : account.isError ? (
            <p className="text-sm text-red-800">
              Failed to load account status:{" "}
              {account.error instanceof ApiError ? account.error.message : "unknown error"}
            </p>
          ) : account.data?.connected ? (
            <ConnectedSummary
              fsUserId={account.data.fs_user_id}
              scope={account.data.scope}
              expiresAt={account.data.expires_at}
              needsRefresh={account.data.needs_refresh}
            />
          ) : (
            <p className="text-sm text-[color:var(--color-ink-700)]">
              Not connected yet. Click the button below to start the OAuth handshake.
            </p>
          )}

          {start.isError ? (
            <p className="text-sm text-red-800" role="alert">
              {start.error instanceof ApiError ? start.error.message : "Failed to start OAuth."}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={() => start.mutate()}
              disabled={start.isPending}
            >
              {account.data?.connected
                ? start.isPending
                  ? "Re-authenticating…"
                  : "Reconnect FamilySearch"
                : start.isPending
                  ? "Redirecting…"
                  : "Connect FamilySearch"}
            </Button>
            {account.data?.connected ? (
              <Button
                type="button"
                variant="secondary"
                size="md"
                onClick={() => disconnect.mutate()}
                disabled={disconnect.isPending}
              >
                {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </main>
  );
}

function ConnectedSummary({
  fsUserId,
  scope,
  expiresAt,
  needsRefresh,
}: {
  fsUserId: string | null;
  scope: string | null;
  expiresAt: string | null;
  needsRefresh: boolean;
}) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
          FamilySearch user
        </dt>
        <dd className="font-mono">{fsUserId ?? "—"}</dd>
      </div>
      <div>
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">Scope</dt>
        <dd className="font-mono">{scope ?? "(default)"}</dd>
      </div>
      <div className="sm:col-span-2">
        <dt className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
          Token expires
        </dt>
        <dd>
          {expiresAt ? new Date(expiresAt).toLocaleString() : "—"}{" "}
          {needsRefresh ? (
            <span className="ml-2 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-900">
              expired — refresh required
            </span>
          ) : null}
        </dd>
      </div>
    </dl>
  );
}
