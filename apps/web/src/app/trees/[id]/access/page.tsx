"use client";

/**
 * /trees/[id]/access — Phase 11.1 owner-facing UI для управления доступом.
 *
 * Левая колонка: список members (email с masking, role, action'ы revoke).
 * Правая колонка: invite form + список pending invitations с copy-link/revoke/resend.
 * Внизу — owner-transfer 2-of-2 (раскрывается по клику, требует ввода обоих email'ов).
 *
 * Auth: до Phase 4.10 (Clerk) auth-stub резолвит current user из X-User-Id
 * header или settings.owner_email; UI пока работает в режиме «локальный
 * owner = single user», что для staging ок. После Phase 4.10 страница
 * автоматически получит JWT-аутентифицированного user'а.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  type Invitation,
  type Member,
  createInvitation,
  fetchInvitations,
  fetchMembers,
  maskEmail,
  resendInvitation,
  revokeInvitation,
  revokeMember,
  transferOwnership,
  updateMemberRole,
} from "@/lib/api";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
};

export default function AccessPage() {
  return (
    <Suspense fallback={null}>
      <AccessPageContent />
    </Suspense>
  );
}

function AccessPageContent() {
  const params = useParams<{ id: string }>();
  const treeId = params.id;
  const queryClient = useQueryClient();

  const members = useQuery({
    queryKey: ["members", treeId],
    queryFn: () => fetchMembers(treeId),
    refetchOnWindowFocus: false,
  });

  const invitations = useQuery({
    queryKey: ["invitations", treeId],
    queryFn: () => fetchInvitations(treeId),
    refetchOnWindowFocus: false,
  });

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["members", treeId] });
    void queryClient.invalidateQueries({ queryKey: ["invitations", treeId] });
  };

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Tree access</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Invite family members as Editor or Viewer. Pending invitations are listed below.
        </p>
      </header>

      <section className="grid gap-6 md:grid-cols-2">
        <MembersCard
          members={members.data?.items ?? []}
          isLoading={members.isLoading}
          isError={members.isError}
          error={members.error}
          onChange={invalidateAll}
        />
        <InvitationsCard
          treeId={treeId}
          invitations={invitations.data?.items ?? []}
          isLoading={invitations.isLoading}
          onChange={invalidateAll}
        />
      </section>

      <section className="mt-10">
        <TransferOwnerCard
          treeId={treeId}
          members={members.data?.items ?? []}
          onTransfer={invalidateAll}
        />
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Members
// ---------------------------------------------------------------------------

function MembersCard({
  members,
  isLoading,
  isError,
  error,
  onChange,
}: {
  members: Member[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onChange: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <CardDescription>
          Active members with their current role. Email is partially masked for privacy.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>
        ) : isError ? (
          <p className="text-sm text-red-800">
            Failed to load members: {error instanceof ApiError ? error.message : "unknown error"}
          </p>
        ) : members.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">
            No members yet — only the owner has access.
          </p>
        ) : (
          <ul className="divide-y divide-[color:var(--color-border)]">
            {members.map((m) => (
              <MemberRow key={m.id} member={m} onChange={onChange} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function MemberRow({ member, onChange }: { member: Member; onChange: () => void }) {
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

  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{member.display_name ?? member.email}</p>
        <p className="truncate text-xs text-[color:var(--color-ink-500)]">
          {maskEmail(member.email)}
        </p>
      </div>
      <Badge variant={isOwner ? "accent" : "neutral"}>
        {ROLE_LABEL[member.role] ?? member.role}
      </Badge>
      {!isOwner ? (
        <div className="flex gap-1">
          {member.role === "viewer" ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => promote.mutate()}
              disabled={promote.isPending}
            >
              Make editor
            </Button>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => demote.mutate()}
              disabled={demote.isPending}
            >
              Make viewer
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              if (window.confirm(`Revoke access for ${member.email}?`)) {
                revoke.mutate();
              }
            }}
            disabled={revoke.isPending}
          >
            Revoke
          </Button>
        </div>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Invitations
// ---------------------------------------------------------------------------

function InvitationsCard({
  treeId,
  invitations,
  isLoading,
  onChange,
}: {
  treeId: string;
  invitations: Invitation[];
  isLoading: boolean;
  onChange: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"editor" | "viewer">("viewer");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => createInvitation(treeId, email, role),
    onSuccess: () => {
      setEmail("");
      setFormError(null);
      onChange();
    },
    onError: (err) => {
      setFormError(err instanceof ApiError ? err.message : "Failed to send invitation");
    },
  });

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!isValidEmail(email)) {
      setFormError("Enter a valid email address");
      return;
    }
    create.mutate();
  };

  const pending = invitations.filter((inv) => inv.accepted_at === null && inv.revoked_at === null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Invite</CardTitle>
        <CardDescription>The invitee receives a one-time link valid for 14 days.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={onSubmit} className="space-y-2" aria-label="Invite form">
          <Input
            // type="text" instead of "email" — мы валидируем сами (isValidEmail),
            // браузерный required-блок мешал бы отображать наш inline-error.
            type="text"
            placeholder="cousin@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-label="Invitee email"
          />
          <fieldset className="flex gap-3 text-sm">
            <legend className="sr-only">Role</legend>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="viewer"
                checked={role === "viewer"}
                onChange={() => setRole("viewer")}
              />
              Viewer
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="editor"
                checked={role === "editor"}
                onChange={() => setRole("editor")}
              />
              Editor
            </label>
          </fieldset>
          {formError ? (
            <p className="text-sm text-red-800" role="alert">
              {formError}
            </p>
          ) : null}
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Sending…" : "Send invitation"}
          </Button>
        </form>

        <div>
          <h2 className="text-sm font-semibold">Pending</h2>
          {isLoading ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>
          ) : pending.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-500)]">No pending invitations.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {pending.map((inv) => (
                <PendingInvitation key={inv.id} invitation={inv} onChange={onChange} />
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function PendingInvitation({
  invitation,
  onChange,
}: {
  invitation: Invitation;
  onChange: () => void;
}) {
  const revoke = useMutation({
    mutationFn: () => revokeInvitation(invitation.id),
    onSuccess: onChange,
  });
  const resend = useMutation({
    mutationFn: () => resendInvitation(invitation.token),
  });

  const copyLink = () => {
    void navigator.clipboard?.writeText(invitation.invite_url);
  };

  return (
    <li className="rounded border border-[color:var(--color-border)] p-2 text-sm">
      <p className="truncate font-medium">{invitation.invitee_email}</p>
      <p className="text-xs text-[color:var(--color-ink-500)]">
        {ROLE_LABEL[invitation.role]} · expires{" "}
        {new Date(invitation.expires_at).toLocaleDateString()}
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={copyLink}>
          Copy link
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => resend.mutate()}
          disabled={resend.isPending}
        >
          {resend.isPending ? "Resending…" : "Resend"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => revoke.mutate()}
          disabled={revoke.isPending}
        >
          Revoke
        </Button>
      </div>
      {resend.isError ? (
        <p className="mt-1 text-xs text-red-700" role="alert">
          {resend.error instanceof ApiError ? resend.error.message : "Resend failed"}
        </p>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Owner transfer (2-of-2)
// ---------------------------------------------------------------------------

function TransferOwnerCard({
  treeId,
  members,
  onTransfer,
}: {
  treeId: string;
  members: Member[];
  onTransfer: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [newOwnerEmail, setNewOwnerEmail] = useState("");
  const [confirmEmail, setConfirmEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  const owner = members.find((m) => m.role === "owner");
  const transferable = members.filter((m) => m.role !== "owner");

  const transfer = useMutation({
    mutationFn: () => transferOwnership(treeId, newOwnerEmail, confirmEmail),
    onSuccess: () => {
      setOpen(false);
      setStep(1);
      setNewOwnerEmail("");
      setConfirmEmail("");
      setError(null);
      onTransfer();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Transfer failed");
    },
  });

  if (!open) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Transfer ownership</CardTitle>
          <CardDescription>
            Make another active member the owner. Two-step confirmation prevents accidents.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            type="button"
            variant="secondary"
            onClick={() => setOpen(true)}
            disabled={transferable.length === 0}
          >
            Start transfer
          </Button>
          {transferable.length === 0 ? (
            <p className="mt-2 text-xs text-[color:var(--color-ink-500)]">
              Add at least one Editor or Viewer before you can transfer ownership.
            </p>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Transfer ownership — step {step} of 2</CardTitle>
        <CardDescription>
          {step === 1
            ? "Choose the new owner."
            : "Type your own email to confirm. This cannot be undone without the new owner agreeing."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {step === 1 ? (
          <>
            <select
              className="w-full rounded border border-[color:var(--color-border)] bg-transparent px-2 py-1 text-sm"
              value={newOwnerEmail}
              onChange={(e) => setNewOwnerEmail(e.target.value)}
              aria-label="New owner"
            >
              <option value="">Select a member…</option>
              {transferable.map((m) => (
                <option key={m.id} value={m.email}>
                  {m.email} ({ROLE_LABEL[m.role]})
                </option>
              ))}
            </select>
            <div className="flex gap-2">
              <Button
                type="button"
                onClick={() => {
                  if (!newOwnerEmail) {
                    setError("Pick a member first");
                    return;
                  }
                  setError(null);
                  setStep(2);
                }}
              >
                Continue
              </Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm">
              Transferring ownership to <strong>{newOwnerEmail}</strong>. Type{" "}
              <code className="rounded bg-[color:var(--color-surface-2,transparent)] px-1">
                {owner?.email ?? "your email"}
              </code>{" "}
              below to confirm.
            </p>
            <Input
              type="email"
              placeholder={owner?.email ?? "your@email.com"}
              value={confirmEmail}
              onChange={(e) => setConfirmEmail(e.target.value)}
              aria-label="Confirm your email"
            />
            {error ? (
              <p className="text-sm text-red-800" role="alert">
                {error}
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                onClick={() => transfer.mutate()}
                disabled={transfer.isPending || !confirmEmail}
              >
                {transfer.isPending ? "Transferring…" : "Transfer ownership"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setStep(1)}>
                Back
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isValidEmail(value: string): boolean {
  // Минимальная RFC 5321 проверка для UI; сервер делает свою валидацию.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}
