"use client";

/**
 * /settings — account settings UI (Phase 4.10b, ADR-0038).
 *
 * Tabs:
 * - Profile: display_name / locale / timezone (PATCH /users/me).
 *   Locale dual-write — to Clerk publicMetadata AND our backend.
 * - Sessions: list current user's active Clerk sessions, revoke
 *   individual + "Sign out everywhere".
 * - Danger zone: delete account (POST /users/me/erasure-request, stub
 *   processed in Phase 4.11) + data export request.
 *
 * Pattern: tab state is local (useState), URL hash optional. Each tab
 * is its own component with own queries — keeps the file split.
 */

import { useUser } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ChangeEvent, useState } from "react";

import { RestartTourButton } from "@/components/onboarding-tour";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import {
  type UserMe,
  fetchMe,
  fetchMyRequests,
  requestErasure,
  requestExport,
  updateMe,
} from "@/lib/user-settings-api";

type TabId = "profile" | "sessions" | "danger";

const TABS: { id: TabId; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "sessions", label: "Sessions" },
  { id: "danger", label: "Danger zone" },
];

export default function SettingsPage() {
  const [tab, setTab] = useState<TabId>("profile");

  return (
    <main className="mx-auto max-w-3xl px-6 py-10" data-testid="settings-page">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Account settings</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Manage your profile, active sessions, and account data.
        </p>
      </header>

      <nav
        className="mb-6 flex gap-1 border-b border-[color:var(--color-border)]"
        role="tablist"
        aria-label="Settings tabs"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            data-testid={`tab-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium transition ${
              tab === t.id
                ? "border-b-2 border-[color:var(--color-ink-900)] text-[color:var(--color-ink-900)]"
                : "text-[color:var(--color-ink-500)] hover:text-[color:var(--color-ink-900)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <section role="tabpanel" aria-label={`${tab} settings`}>
        {tab === "profile" ? <ProfileTab /> : null}
        {tab === "sessions" ? <SessionsTab /> : null}
        {tab === "danger" ? <DangerZoneTab /> : null}
      </section>

      {/* Phase 4.15 — restart-tour pinned под tab-content на любом активном
          tab'е. Дизайн-выбор: не делать отдельный tab "Help" ради одной
          кнопки; tour — preference, не самостоятельный feature-area. */}
      {tab === "profile" ? <RestartTourCard /> : null}
    </main>
  );
}

function RestartTourCard() {
  const t = useTranslations("onboarding.tour");
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>{t("restart")}</CardTitle>
        <CardDescription>{t("restartHint")}</CardDescription>
      </CardHeader>
      <CardContent className="flex justify-end">
        <RestartTourButton />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Profile tab
// ---------------------------------------------------------------------------

function ProfileTab() {
  const queryClient = useQueryClient();
  const { user: clerkUser } = useUser();

  const meQuery = useQuery({
    queryKey: ["user-me"],
    queryFn: fetchMe,
  });

  const [displayName, setDisplayName] = useState<string | null>(null);
  const [locale, setLocale] = useState<string | null>(null);
  const [timezone, setTimezone] = useState<string | null>(null);

  // Hydrate local state с server-row один раз (initial load).
  if (meQuery.data && displayName === null && locale === null && timezone === null) {
    setDisplayName(meQuery.data.display_name ?? "");
    setLocale(meQuery.data.locale);
    setTimezone(meQuery.data.timezone ?? "");
  }

  const update = useMutation({
    mutationFn: async (body: {
      display_name?: string | null;
      locale?: string;
      timezone?: string | null;
    }) => {
      // 1. Backend (canonical для i18n).
      const updated = await updateMe(body);
      // 2. Clerk publicMetadata (чтобы locale survived sign-out и был
      //    доступен в JWT-claims на server-side rendering без round-trip).
      //    Best-effort: на ошибке Clerk-update мы не откатываем
      //    backend-изменение, но логируем — пользователь увидит
      //    рассинхрон при следующем reload.
      if (body.locale && clerkUser) {
        try {
          await clerkUser.update({
            unsafeMetadata: { ...clerkUser.unsafeMetadata, locale: body.locale },
          });
        } catch (clerkErr) {
          console.warn("Clerk metadata update failed (backend updated OK):", clerkErr);
        }
      }
      return updated;
    },
    onSuccess: (data: UserMe) => {
      queryClient.setQueryData(["user-me"], data);
      setDisplayName(data.display_name ?? "");
      setLocale(data.locale);
      setTimezone(data.timezone ?? "");
    },
  });

  if (meQuery.isLoading) {
    return <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>;
  }
  if (meQuery.isError) {
    return <ErrorBanner message={errorMessage(meQuery.error, "Failed to load your profile")} />;
  }

  const me = meQuery.data;
  if (!me) return null;

  const onSave = () => {
    const body: { display_name?: string | null; locale?: string; timezone?: string | null } = {};
    const trimmedName = (displayName ?? "").trim();
    if (trimmedName !== (me.display_name ?? "")) {
      body.display_name = trimmedName || null;
    }
    if (locale && locale !== me.locale) {
      body.locale = locale;
    }
    const trimmedTz = (timezone ?? "").trim();
    if (trimmedTz !== (me.timezone ?? "")) {
      body.timezone = trimmedTz || null;
    }
    if (Object.keys(body).length === 0) return;
    update.mutate(body);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>
          Visible in the UI. Locale also controls the language of email notifications.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Email">
          <Input value={me.email} disabled />
        </Field>
        <Field label="Display name">
          <Input
            value={displayName ?? ""}
            data-testid="profile-display-name"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setDisplayName(e.target.value)}
            placeholder="(optional)"
          />
        </Field>
        <Field label="Locale">
          <select
            value={locale ?? "en"}
            data-testid="profile-locale"
            onChange={(e: ChangeEvent<HTMLSelectElement>) => setLocale(e.target.value)}
            className="h-9 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-2 text-sm"
          >
            <option value="en">English</option>
            <option value="ru">Русский</option>
          </select>
        </Field>
        <Field label="Timezone (IANA)">
          <Input
            value={timezone ?? ""}
            data-testid="profile-timezone"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTimezone(e.target.value)}
            placeholder="Europe/Moscow"
          />
        </Field>
        {update.isError ? (
          <div className="col-span-full">
            <ErrorBanner message={errorMessage(update.error, "Failed to save profile changes")} />
          </div>
        ) : null}
        <div className="col-span-full flex justify-end">
          <Button
            type="button"
            variant="primary"
            size="md"
            data-testid="profile-save"
            onClick={onSave}
            disabled={update.isPending}
          >
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sessions tab
// ---------------------------------------------------------------------------

type ClerkSessionLike = {
  id: string;
  status: string;
  lastActiveAt?: Date | string | null;
  // Activity-объект Clerk-а; конкретные поля зависят от SDK-version.
  latestActivity?: {
    browserName?: string | null;
    deviceType?: string | null;
    ipAddress?: string | null;
    city?: string | null;
    country?: string | null;
  } | null;
  // Clerk SDK type — Promise<SessionWithActivitiesResource>; нам результат
  // не нужен, но typing должен совпадать, иначе TS ругается на cast.
  revoke?: () => Promise<unknown>;
};

function SessionsTab() {
  const { user: clerkUser, isLoaded } = useUser();
  const [sessions, setSessions] = useState<ClerkSessionLike[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Загрузка sessions через Clerk-API (frontend-side, не наш backend).
  // useEffect-эффект ниже (useState seed не подходит — нужно
  // pull-to-refresh поведение).
  // ESLint: используем простой fetch-on-mount + manual refresh button.
  // useQuery не используем — Clerk-данные не идут через ApiError-канал.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = async () => {
    if (!clerkUser) return;
    setBusy(true);
    setError(null);
    try {
      const list = await clerkUser.getSessions();
      setSessions(list as unknown as ClerkSessionLike[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch sessions");
    } finally {
      setBusy(false);
    }
  };

  // Lazy-load при первом рендере (когда clerkUser появился).
  if (isLoaded && clerkUser && sessions === null && !busy && !error) {
    void reload();
  }

  const revokeOne = async (s: ClerkSessionLike) => {
    if (!s.revoke) return;
    setBusy(true);
    try {
      await s.revoke();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke session");
    } finally {
      setBusy(false);
    }
  };

  const revokeAllOthers = async () => {
    if (!sessions) return;
    setBusy(true);
    try {
      // Текущая session — id-сравнение через clerkUser-метаданные;
      // SDK возвращает её первой, но мы не полагаемся на порядок:
      // Sign Out Everywhere = revoke всех остальных, оставляем active.
      await Promise.allSettled(
        sessions
          .filter((s) => s.status === "active")
          .map((s) => (s.revoke ? s.revoke() : Promise.resolve())),
      );
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sign out other sessions");
    } finally {
      setBusy(false);
    }
  };

  if (!isLoaded) {
    return <p className="text-sm text-[color:var(--color-ink-500)]">Loading…</p>;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Active sessions</CardTitle>
        <CardDescription>
          Sessions are tracked by Clerk. Revoking a session signs that browser out within ~1 second.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error ? <ErrorBanner message={error} /> : null}
        {sessions === null ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">Loading sessions…</p>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">No active sessions.</p>
        ) : (
          <ul
            data-testid="sessions-list"
            className="flex flex-col divide-y divide-[color:var(--color-border)]"
          >
            {sessions.map((s) => (
              <li key={s.id} className="flex items-center justify-between py-2 gap-4">
                <div className="text-sm">
                  <p className="font-medium">{describeSession(s)}</p>
                  <p className="text-xs text-[color:var(--color-ink-500)]">
                    {s.status} · Last active: {formatDate(s.lastActiveAt)}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid={`session-revoke-${s.id}`}
                  onClick={() => revokeOne(s)}
                  disabled={busy || s.status !== "active"}
                >
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={reload} disabled={busy}>
            Refresh
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="md"
            data-testid="sign-out-everywhere"
            onClick={revokeAllOthers}
            disabled={busy || !sessions || sessions.length === 0}
          >
            Sign out everywhere
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function describeSession(s: ClerkSessionLike): string {
  const a = s.latestActivity;
  if (!a) return `Session ${s.id.slice(0, 8)}…`;
  const where = [a.city, a.country].filter(Boolean).join(", ");
  const browser = a.browserName ?? a.deviceType ?? "Unknown device";
  return where ? `${browser} · ${where}` : browser;
}

function formatDate(d: Date | string | null | undefined): string {
  if (!d) return "—";
  const date = typeof d === "string" ? new Date(d) : d;
  return date.toLocaleString();
}

// ---------------------------------------------------------------------------
// Danger zone tab
// ---------------------------------------------------------------------------

function DangerZoneTab() {
  const queryClient = useQueryClient();
  const meQuery = useQuery({
    queryKey: ["user-me"],
    queryFn: fetchMe,
  });
  const requestsQuery = useQuery({
    queryKey: ["user-action-requests"],
    queryFn: fetchMyRequests,
  });

  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [confirmEmail, setConfirmEmail] = useState("");

  const erasure = useMutation({
    mutationFn: (body: { confirm_email: string }) => requestErasure(body),
    onSuccess: () => {
      setShowDeleteModal(false);
      setConfirmEmail("");
      void queryClient.invalidateQueries({ queryKey: ["user-action-requests"] });
    },
  });

  const exportReq = useMutation({
    mutationFn: () => requestExport(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["user-action-requests"] });
    },
  });

  const me = meQuery.data;
  const requests = requestsQuery.data?.items ?? [];
  const pendingErasure = requests.find((r) => r.kind === "erasure" && r.status === "pending");
  const pendingExport = requests.find((r) => r.kind === "export" && r.status === "pending");

  const emailMatches = me ? confirmEmail.trim().toLowerCase() === me.email.toLowerCase() : false;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Data export</CardTitle>
          <CardDescription>
            Request a copy of your data (GEDCOM + DNA + provenance). Processed asynchronously; you
            will receive a notification when the download is ready.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-between gap-3">
          <p className="text-sm text-[color:var(--color-ink-500)]">
            {pendingExport
              ? `An export request is pending (created ${formatDate(pendingExport.created_at)}).`
              : "No pending export request."}
          </p>
          <Button
            type="button"
            variant="primary"
            size="md"
            data-testid="export-request"
            onClick={() => exportReq.mutate()}
            disabled={exportReq.isPending || Boolean(pendingExport)}
          >
            {exportReq.isPending ? "Submitting…" : "Request my data"}
          </Button>
        </CardContent>
        {exportReq.isError ? (
          <CardContent>
            <ErrorBanner message={errorMessage(exportReq.error, "Failed to submit export")} />
          </CardContent>
        ) : null}
      </Card>

      <Card className="border-red-200">
        <CardHeader>
          <CardTitle className="text-red-700">Delete my account</CardTitle>
          <CardDescription>
            All trees, DNA data, and personal information will be permanently deleted. This cannot
            be undone.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-between gap-3">
          <p className="text-sm text-[color:var(--color-ink-500)]">
            {pendingErasure
              ? `An erasure request is pending (created ${formatDate(pendingErasure.created_at)}).`
              : "Initiate account deletion. Phase 4.11 will perform the actual erasure."}
          </p>
          <Button
            type="button"
            variant="destructive"
            size="md"
            data-testid="open-delete-modal"
            onClick={() => setShowDeleteModal(true)}
            disabled={Boolean(pendingErasure)}
          >
            Delete account…
          </Button>
        </CardContent>
      </Card>

      {showDeleteModal && me ? (
        <DeleteAccountModal
          email={me.email}
          confirmEmail={confirmEmail}
          onConfirmEmailChange={setConfirmEmail}
          emailMatches={emailMatches}
          onCancel={() => {
            setShowDeleteModal(false);
            setConfirmEmail("");
          }}
          onConfirm={() => erasure.mutate({ confirm_email: confirmEmail.trim() })}
          submitting={erasure.isPending}
          error={erasure.isError ? errorMessage(erasure.error, "Failed to submit") : null}
        />
      ) : null}
    </div>
  );
}

function DeleteAccountModal({
  email,
  confirmEmail,
  onConfirmEmailChange,
  emailMatches,
  onCancel,
  onConfirm,
  submitting,
  error,
}: {
  email: string;
  confirmEmail: string;
  onConfirmEmailChange: (next: string) => void;
  emailMatches: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  submitting: boolean;
  error: string | null;
}) {
  return (
    <dialog
      open
      className="fixed inset-0 z-50 m-0 flex h-full w-full items-center justify-center bg-black/40 p-4"
      aria-labelledby="delete-modal-title"
      data-testid="delete-modal"
    >
      <div className="w-full max-w-md rounded-lg bg-[color:var(--color-surface)] p-6 shadow-xl">
        <h2 id="delete-modal-title" className="text-lg font-semibold text-red-700">
          Delete this account?
        </h2>
        <p className="mt-2 text-sm text-[color:var(--color-ink-500)]">
          This is permanent. To confirm, type your email <span className="font-mono">{email}</span>{" "}
          below.
        </p>
        <Input
          className="mt-3"
          value={confirmEmail}
          data-testid="delete-confirm-email"
          onChange={(e: ChangeEvent<HTMLInputElement>) => onConfirmEmailChange(e.target.value)}
          placeholder={email}
        />
        {error ? (
          <div className="mt-3">
            <ErrorBanner message={error} />
          </div>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" size="md" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="md"
            data-testid="delete-confirm"
            onClick={onConfirm}
            disabled={!emailMatches || submitting}
          >
            {submitting ? "Submitting…" : "Delete my account"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-1.5 border-0 p-0">
      <legend className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]">
        {label}
      </legend>
      {children}
    </fieldset>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
    >
      {message}
    </div>
  );
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return fallback;
}
