"use client";

/**
 * Phase 11.1 — client-side accept UI. Получает уже-resolved lookup от server'а
 * (`page.tsx`); отвечает за Clerk-state и mutation.
 *
 * Четыре state'а из task spec'а:
 *   1. Token invalid/expired/not-found → ErrorMessage + back-to-dashboard.
 *   2. Token valid, user not signed in → Clerk SignIn modal (signed-out branch).
 *   3. Token valid, signed in, email mismatch → предупреждение + sign-out CTA.
 *   4. Token valid, signed in, email match → Accept button → POST accept + redirect.
 *
 * Accepted-but-not-yet-redirected case (`accepted_at != null`) показывает
 * «уже принято» с кнопкой «Open tree» — это покрывает повторное открытие
 * email-ссылки тем же user'ом.
 */

import { SignInButton, SignUpButton, SignedIn, SignedOut, useUser } from "@clerk/nextjs";
import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ErrorMessage } from "@/components/error-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, acceptInvitation } from "@/lib/api";

export type LookupOk = {
  invitee_email: string;
  role: "owner" | "editor" | "viewer";
  tree_id: string;
  tree_name: string;
  inviter_display_name: string;
  expires_at: string;
  accepted_at: string | null;
};

export type LookupResult =
  | { kind: "ok"; data: LookupOk }
  | { kind: "not_found" }
  | { kind: "invalid" }
  | { kind: "error" };

export function InvitationAcceptClient({
  token,
  lookup,
}: {
  token: string;
  lookup: LookupResult;
}) {
  const t = useTranslations("sharing.accept");

  if (lookup.kind === "not_found") {
    return (
      <Shell title={t("notFoundTitle")}>
        <CardDescription>{t("notFoundBody")}</CardDescription>
        <DashboardLink label={t("goToDashboard")} />
      </Shell>
    );
  }

  if (lookup.kind === "invalid") {
    return (
      <Shell title={t("invalidTitle")}>
        <CardDescription>{t("invalidBody")}</CardDescription>
        <DashboardLink label={t("goToDashboard")} />
      </Shell>
    );
  }

  if (lookup.kind === "error") {
    return (
      <Shell title={t("title")}>
        <ErrorMessage code="generic" />
        <DashboardLink label={t("goToDashboard")} />
      </Shell>
    );
  }

  return <ValidInvitationView token={token} data={lookup.data} />;
}

// ---------------------------------------------------------------------------
// Valid invitation — drives auth + accept logic
// ---------------------------------------------------------------------------

function ValidInvitationView({ token, data }: { token: string; data: LookupOk }) {
  const t = useTranslations("sharing.accept");
  const tRoles = useTranslations("sharing.roles");

  return (
    <Shell title={t("title")}>
      <div className="space-y-2 text-sm">
        <p>{t("invitedAs", { role: tRoles(data.role) })}</p>
        <p>
          {t("invitedToTree", { treeName: data.tree_name })}{" "}
          <Badge variant="neutral">{tRoles(data.role)}</Badge>
        </p>
        <p className="text-[color:var(--color-ink-500)]">
          {t("invitedBy", { inviter: data.inviter_display_name })}
        </p>
        <p className="text-xs text-[color:var(--color-ink-500)]">
          {t("expiresAt", { date: new Date(data.expires_at).toLocaleDateString() })}
        </p>
      </div>

      <SignedOut>
        <SignedOutBlock token={token} />
      </SignedOut>

      <SignedIn>
        <SignedInBlock token={token} data={data} />
      </SignedIn>
    </Shell>
  );
}

function SignedOutBlock({ token }: { token: string }) {
  const t = useTranslations("sharing.accept");
  // После sign-in возвращаемся на ту же страницу — Clerk поддерживает
  // forceRedirectUrl на SignInButton.
  const redirectUrl = `/invitations/${encodeURIComponent(token)}`;
  return (
    <div className="space-y-3 rounded-md border border-[color:var(--color-border)] p-4">
      <h2 className="text-sm font-semibold">{t("signInPrompt")}</h2>
      <p className="text-sm text-[color:var(--color-ink-500)]">{t("signInDescription")}</p>
      <div className="flex flex-wrap gap-2">
        <SignInButton mode="modal" forceRedirectUrl={redirectUrl}>
          <Button type="button" variant="primary" size="md">
            {t("signInButton")}
          </Button>
        </SignInButton>
        <SignUpButton mode="modal" forceRedirectUrl={redirectUrl}>
          <Button type="button" variant="secondary" size="md">
            {t("signUpButton")}
          </Button>
        </SignUpButton>
      </div>
    </div>
  );
}

function SignedInBlock({ token, data }: { token: string; data: LookupOk }) {
  const t = useTranslations("sharing.accept");
  const router = useRouter();
  const { user, isLoaded } = useUser();

  const accept = useMutation({
    mutationFn: () => acceptInvitation(token),
    onSuccess: (resp) => {
      router.replace(`/trees/${resp.tree_id}`);
    },
  });

  // Уже принято этим же user'ом ранее (или одним из его аккаунтов) — показываем
  // «open tree» вместо accept-кнопки. Backend все равно отдаст 200 idempotent
  // если нажать accept, но проще не давать кнопку.
  if (data.accepted_at !== null) {
    return (
      <div className="space-y-3 rounded-md border border-[color:var(--color-border)] p-4">
        <h2 className="text-sm font-semibold">{t("alreadyAcceptedTitle")}</h2>
        <p className="text-sm text-[color:var(--color-ink-500)]">{t("alreadyAcceptedBody")}</p>
        <Button asChild variant="primary" size="md">
          <Link href={`/trees/${data.tree_id}`}>{t("goToTree")}</Link>
        </Button>
      </div>
    );
  }

  if (!isLoaded) {
    return <p className="text-sm text-[color:var(--color-ink-500)]">{t("loadingLookup")}</p>;
  }

  const currentEmail = user?.primaryEmailAddress?.emailAddress?.toLowerCase() ?? null;
  const invitedEmail = data.invitee_email.toLowerCase();

  if (currentEmail !== null && currentEmail !== invitedEmail) {
    return (
      <EmailMismatchBlock currentEmail={currentEmail} invitedEmail={invitedEmail} token={token} />
    );
  }

  if (accept.isSuccess) {
    return (
      <div className="space-y-1 rounded-md border border-emerald-200 bg-emerald-50 p-4">
        <p className="text-sm font-semibold text-emerald-900">{t("successTitle")}</p>
        <p className="text-sm text-emerald-800">{t("successBody")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Button
        type="button"
        variant="primary"
        size="md"
        onClick={() => accept.mutate()}
        disabled={accept.isPending}
      >
        {accept.isPending ? t("accepting") : t("acceptButton")}
      </Button>
      {accept.isError ? <AcceptErrorBlock error={accept.error} /> : null}
    </div>
  );
}

function EmailMismatchBlock({
  currentEmail,
  invitedEmail,
  token,
}: {
  currentEmail: string;
  invitedEmail: string;
  token: string;
}) {
  const t = useTranslations("sharing.accept");
  // Sign-out + redirect обратно на ту же страницу заставит Clerk показать
  // sign-in флоу с подсказкой по invited email'у. Не делаем сами sign-out
  // (нужна @clerk/nextjs/server action или useClerk), оставляем кнопку
  // CTA-only — owner ожидает что юзер откроет sign-out из user-menu.
  return (
    <div className="space-y-3 rounded-md border border-amber-200 bg-amber-50 p-4 text-amber-900">
      <h2 className="text-sm font-semibold">{t("emailMismatchTitle")}</h2>
      <p className="text-sm">{t("emailMismatchBody", { currentEmail, invitedEmail })}</p>
      <p className="text-xs">
        <Link
          href={`/sign-in?redirect_url=${encodeURIComponent(`/invitations/${token}`)}`}
          className="underline"
        >
          {t("emailMismatchAction", { invitedEmail })}
        </Link>
      </p>
    </div>
  );
}

function AcceptErrorBlock({ error }: { error: unknown }) {
  const t = useTranslations("sharing.accept");
  if (error instanceof ApiError) {
    if (error.status === 410) {
      return <ErrorMessage code="generic" />;
    }
    if (error.status === 401) {
      return <ErrorMessage code="unauthorized" />;
    }
    return (
      <p className="text-sm text-red-800" role="alert">
        {error.message}
      </p>
    );
  }
  return (
    <p className="text-sm text-red-800" role="alert">
      {t("errorGeneric")}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Card>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">{children}</CardContent>
      </Card>
    </main>
  );
}

function DashboardLink({ label }: { label: string }) {
  return (
    <div>
      <Button asChild variant="secondary" size="sm">
        <Link href="/dashboard">{label}</Link>
      </Button>
    </div>
  );
}
