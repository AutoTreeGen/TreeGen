"use client";

/**
 * /invite/[token] — Phase 11.1 invitation accept landing.
 *
 * Public route (без auth-gate'а). Принимает unauthenticated visitor'ов.
 * До Phase 4.10 (Clerk) auth-стаб резолвит current user через X-User-Id
 * header или settings.owner_email — этот path рассчитан на in-staging
 * single-user сценарий + готовится к real Clerk SSO.
 *
 * Поведение:
 *   - Sign-in detection: до Phase 4.10 у нас нет надёжного «is signed in»
 *     boolean. Стратегия — попытка accept'а; если backend вернёт 401
 *     (после Phase 4.10), редиректим на /sign-in?redirect=...
 *   - 410 expired/revoked → дружественное сообщение, нет accept-кнопки.
 *   - 201/200 happy path → автоматический redirect на /trees/{tree_id}/persons.
 *   - 409 conflict (другой user уже accept'нул) — показываем error.
 */

import { useMutation } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, acceptInvitation } from "@/lib/api";

export default function InviteAcceptPage() {
  return (
    <Suspense fallback={null}>
      <InviteAcceptContent />
    </Suspense>
  );
}

function InviteAcceptContent() {
  const params = useParams<{ token: string }>();
  const router = useRouter();
  const token = params.token;

  const accept = useMutation({
    mutationFn: () => acceptInvitation(token),
    onSuccess: (data) => {
      // Phase 11.1: redirect на tree-detail. Phase 11.1b добавит /trees/{id}/dashboard;
      // /persons — текущая посещаемая страница в trees/[id]/* tree.
      router.replace(`/trees/${data.tree_id}/persons`);
    },
  });

  useEffect(() => {
    // Auto-fire accept на mount: если invitation валиден — backend проставит
    // accepted_at + создаст membership; если невалиден — мы покажем error.
    // mutate identity stable; ESLint biome требует deps но useMutation.mutate
    // не реактивная функция — добавляем в deps без переплаты.
    accept.mutate();
  }, [accept.mutate]);

  // Phase 4.10 hook: 401 = пользователь не залогинен → редирект в Clerk sign-in.
  useEffect(() => {
    if (accept.isError && accept.error instanceof ApiError && accept.error.status === 401) {
      router.replace(`/sign-in?redirect=/invite/${token}`);
    }
  }, [accept.isError, accept.error, router, token]);

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Card>
        <CardHeader>
          <CardTitle>Accept invitation</CardTitle>
          <CardDescription>You were invited to collaborate on a family tree.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {accept.isPending ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">Joining the tree…</p>
          ) : accept.isError ? (
            <AcceptError error={accept.error} onRetry={() => accept.mutate()} />
          ) : accept.isSuccess ? (
            <p className="text-sm text-emerald-800">Welcome aboard! Redirecting you to the tree…</p>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}

function AcceptError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const status = error instanceof ApiError ? error.status : null;
  const message = error instanceof ApiError ? error.message : "Unknown error";

  if (status === 401) {
    // Redirect handled by useEffect; render fallback message in case redirect lags.
    return (
      <p className="text-sm text-[color:var(--color-ink-500)]">
        Please sign in to accept this invitation. Redirecting…
      </p>
    );
  }

  if (status === 410) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-amber-900">
          This invitation has expired or was revoked. Ask the owner for a new one.
        </p>
        <p className="text-xs text-[color:var(--color-ink-500)]">{message}</p>
      </div>
    );
  }

  if (status === 409) {
    return (
      <p className="text-sm text-red-800">
        Someone else already accepted this invitation. If that wasn&apos;t you, contact the tree
        owner.
      </p>
    );
  }

  if (status === 404) {
    return (
      <p className="text-sm text-red-800">
        Invitation not found. The link may be wrong or already deleted.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-red-800" role="alert">
        Could not accept invitation: {message}
      </p>
      <Button type="button" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
