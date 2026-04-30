"use client";

/**
 * Phase 11.1 — owner-only sharing UI: members + pending invitations + invite form.
 *
 * Распределение ответственности:
 * - `/trees/[id]/sharing` (этот файл) — список members, pending invites, invite form.
 * - `/trees/[id]/access` (Phase 11.0) — расширенный экран с public-link tab'ом и
 *   ownership-transfer'ом; остаётся отдельной страницей до Phase 11.1c.
 *
 * Auth-стратегия:
 * - 401 от backend → redirect на /sign-in (через auth-провайдер в lib/api.ts).
 * - 403 → рендерим i18n-aware «owner-only» state, без редиректа.
 * - Любая другая ошибка → ErrorMessage code="generic" с retry.
 *
 * TanStack Query хранит members + invitations отдельными query-key'ями;
 * mutation'ы инвалидируют оба, чтобы UI остался консистентным после
 * любого create/revoke/role-change.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ErrorMessage } from "@/components/error-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  AuthError,
  type Invitation,
  type Member,
  type ShareRole,
  createInvitation,
  fetchInvitations,
  fetchMembers,
  revokeInvitation,
  revokeMember,
  updateMemberRole,
} from "@/lib/api";

export default function SharingPage() {
  return (
    <Suspense fallback={null}>
      <SharingPageContent />
    </Suspense>
  );
}

function SharingPageContent() {
  const t = useTranslations("sharing");
  const params = useParams<{ id: string }>();
  const treeId = params.id;
  const queryClient = useQueryClient();
  const [toast, setToast] = useState<string | null>(null);

  const members = useQuery({
    queryKey: ["sharing", "members", treeId],
    queryFn: () => fetchMembers(treeId),
    refetchOnWindowFocus: false,
  });

  const invitations = useQuery({
    queryKey: ["sharing", "invitations", treeId],
    queryFn: () => fetchInvitations(treeId),
    refetchOnWindowFocus: false,
  });

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["sharing", "members", treeId] });
    void queryClient.invalidateQueries({ queryKey: ["sharing", "invitations", treeId] });
  };

  const isForbidden =
    (members.error instanceof AuthError && members.error.status === 403) ||
    (invitations.error instanceof AuthError && invitations.error.status === 403);

  if (isForbidden) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-16">
        <Card>
          <CardHeader>
            <CardTitle>{t("owner.forbiddenTitle")}</CardTitle>
            <CardDescription>{t("owner.forbiddenBody")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ErrorMessage code="forbidden" />
          </CardContent>
        </Card>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{t("owner.title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("owner.subtitle")}</p>
      </header>

      {toast ? (
        <output className="mb-4 block rounded-md border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-900">
          {toast}
        </output>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <MembersSection
          members={members.data?.items ?? []}
          isLoading={members.isLoading}
          isError={members.isError && !isForbidden}
          onRetry={() => void members.refetch()}
          onChange={invalidateAll}
        />
        <InvitationsSection
          invitations={invitations.data?.items ?? []}
          isLoading={invitations.isLoading}
          isError={invitations.isError && !isForbidden}
          onRetry={() => void invitations.refetch()}
          onChange={invalidateAll}
        />
      </div>

      <section className="mt-8">
        <InviteForm
          treeId={treeId}
          onSuccess={(message) => {
            setToast(message);
            invalidateAll();
            window.setTimeout(() => setToast(null), 4000);
          }}
        />
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Members
// ---------------------------------------------------------------------------

function MembersSection({
  members,
  isLoading,
  isError,
  onRetry,
  onChange,
}: {
  members: Member[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onChange: () => void;
}) {
  const t = useTranslations("sharing");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("members.heading")}</CardTitle>
        <CardDescription>{t("members.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("owner.loading")}</p>
        ) : isError ? (
          <ErrorMessage code="generic" onRetry={onRetry} />
        ) : members.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("members.empty")}</p>
        ) : (
          <table className="w-full text-left text-sm" aria-label={t("members.heading")}>
            <thead>
              <tr className="border-b border-[color:var(--color-border)] text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                <th className="py-2 pr-3 font-medium">{t("members.columnPerson")}</th>
                <th className="py-2 pr-3 font-medium">{t("members.columnRole")}</th>
                <th className="py-2 pr-3 font-medium">{t("members.columnJoined")}</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">{t("members.columnActions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <MemberRow key={m.id} member={m} onChange={onChange} />
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function MemberRow({ member, onChange }: { member: Member; onChange: () => void }) {
  const t = useTranslations("sharing");
  const isOwner = member.role === "owner";

  const promote = useMutation({
    mutationFn: () => updateMemberRole(member.id, "editor"),
    onSuccess: onChange,
  });
  const demote = useMutation({
    mutationFn: () => updateMemberRole(member.id, "viewer"),
    onSuccess: onChange,
  });
  const revoke = useMutation({
    mutationFn: () => revokeMember(member.id),
    onSuccess: onChange,
  });

  const initial = (member.display_name ?? member.email).slice(0, 1).toUpperCase();

  return (
    <tr className="border-b border-[color:var(--color-border)] last:border-0">
      <td className="py-3 pr-3">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-surface-2,rgba(15,23,42,0.06))] text-xs font-semibold"
          >
            {initial}
          </span>
          <div className="min-w-0">
            <p className="truncate font-medium">{member.display_name ?? member.email}</p>
            <p className="truncate text-xs text-[color:var(--color-ink-500)]">{member.email}</p>
          </div>
        </div>
      </td>
      <td className="py-3 pr-3">
        <Badge variant={isOwner ? "accent" : "neutral"}>{t(`roles.${member.role}` as const)}</Badge>
      </td>
      <td className="py-3 pr-3 text-xs text-[color:var(--color-ink-500)]">
        {new Date(member.joined_at).toLocaleDateString()}
      </td>
      <td className="py-3">
        {isOwner ? null : (
          <div className="flex flex-wrap justify-end gap-1">
            {member.role === "viewer" ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => promote.mutate()}
                disabled={promote.isPending}
              >
                {t("members.promoteToEditor")}
              </Button>
            ) : (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => demote.mutate()}
                disabled={demote.isPending}
              >
                {t("members.demoteToViewer")}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                if (window.confirm(t("members.revokeConfirm", { email: member.email }))) {
                  revoke.mutate();
                }
              }}
              disabled={revoke.isPending}
            >
              {revoke.isPending ? t("members.revokePending") : t("members.revoke")}
            </Button>
          </div>
        )}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Pending invitations
// ---------------------------------------------------------------------------

function InvitationsSection({
  invitations,
  isLoading,
  isError,
  onRetry,
  onChange,
}: {
  invitations: Invitation[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onChange: () => void;
}) {
  const t = useTranslations("sharing");
  // pending = ещё не accepted и ещё не revoked.
  const pending = invitations.filter((inv) => inv.accepted_at === null && inv.revoked_at === null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("invitations.heading")}</CardTitle>
        <CardDescription>{t("invitations.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("owner.loading")}</p>
        ) : isError ? (
          <ErrorMessage code="generic" onRetry={onRetry} />
        ) : pending.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("invitations.empty")}</p>
        ) : (
          <table className="w-full text-left text-sm" aria-label={t("invitations.heading")}>
            <thead>
              <tr className="border-b border-[color:var(--color-border)] text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
                <th className="py-2 pr-3 font-medium">{t("invitations.columnEmail")}</th>
                <th className="py-2 pr-3 font-medium">{t("invitations.columnRole")}</th>
                <th className="py-2 pr-3 font-medium">{t("invitations.columnSent")}</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">{t("invitations.columnActions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {pending.map((inv) => (
                <InvitationRow key={inv.id} invitation={inv} onChange={onChange} />
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function InvitationRow({
  invitation,
  onChange,
}: {
  invitation: Invitation;
  onChange: () => void;
}) {
  const t = useTranslations("sharing");
  const revoke = useMutation({
    mutationFn: () => revokeInvitation(invitation.id),
    onSuccess: onChange,
  });

  return (
    <tr className="border-b border-[color:var(--color-border)] last:border-0">
      <td className="py-3 pr-3 align-top">
        <p className="truncate font-medium">{invitation.invitee_email}</p>
      </td>
      <td className="py-3 pr-3 align-top">
        <Badge variant="neutral">{t(`roles.${invitation.role}` as const)}</Badge>
      </td>
      <td className="py-3 pr-3 align-top text-xs text-[color:var(--color-ink-500)]">
        {new Date(invitation.created_at).toLocaleDateString()}
      </td>
      <td className="py-3 text-right align-top">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            if (
              window.confirm(t("invitations.revokeConfirm", { email: invitation.invitee_email }))
            ) {
              revoke.mutate();
            }
          }}
          disabled={revoke.isPending}
        >
          {t("invitations.revoke")}
        </Button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Invite form
// ---------------------------------------------------------------------------

function InviteForm({
  treeId,
  onSuccess,
}: {
  treeId: string;
  onSuccess: (message: string) => void;
}) {
  const t = useTranslations("sharing");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<ShareRole, "owner">>("viewer");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const successMessage = t("invite.successToast");

  const create = useMutation({
    mutationFn: () => createInvitation(treeId, email, role),
    onSuccess: () => {
      setEmail("");
      setMessage("");
      setError(null);
      onSuccess(successMessage);
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(t("invite.errorGeneric"));
      }
    },
  });

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!isValidEmail(email)) {
      setError(t("invite.errorInvalidEmail"));
      return;
    }
    setError(null);
    create.mutate();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("invite.heading")}</CardTitle>
        <CardDescription>{t("invite.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={onSubmit}
          className="grid gap-4 md:grid-cols-2"
          aria-label={t("invite.heading")}
        >
          <div className="md:col-span-1">
            <label
              className="mb-1 block text-xs font-medium text-[color:var(--color-ink-500)]"
              htmlFor="sharing-invite-email"
            >
              {t("invite.emailLabel")}
            </label>
            <Input
              id="sharing-invite-email"
              type="text"
              placeholder={t("invite.emailPlaceholder")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-label={t("invite.emailLabel")}
            />
          </div>
          <div className="md:col-span-1">
            <span className="mb-1 block text-xs font-medium text-[color:var(--color-ink-500)]">
              {t("invite.roleLabel")}
            </span>
            <fieldset className="space-y-1 text-sm">
              <legend className="sr-only">{t("invite.roleLabel")}</legend>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="sharing-invite-role"
                  value="viewer"
                  checked={role === "viewer"}
                  onChange={() => setRole("viewer")}
                />
                {t("invite.roleViewer")}
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="sharing-invite-role"
                  value="editor"
                  checked={role === "editor"}
                  onChange={() => setRole("editor")}
                />
                {t("invite.roleEditor")}
              </label>
            </fieldset>
          </div>
          <div className="md:col-span-2">
            <label
              className="mb-1 block text-xs font-medium text-[color:var(--color-ink-500)]"
              htmlFor="sharing-invite-message"
            >
              {t("invite.messageLabel")}
            </label>
            <textarea
              id="sharing-invite-message"
              rows={3}
              className="w-full rounded-md border border-[color:var(--color-border)] bg-transparent px-3 py-2 text-sm"
              placeholder={t("invite.messagePlaceholder")}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              aria-label={t("invite.messageLabel")}
            />
          </div>
          {error ? (
            <div className="md:col-span-2">
              <ErrorMessage code="generic" />
              <p className="mt-1 text-sm text-red-800" role="alert">
                {error}
              </p>
            </div>
          ) : null}
          <div className="md:col-span-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? t("invite.submitting") : t("invite.submit")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}
