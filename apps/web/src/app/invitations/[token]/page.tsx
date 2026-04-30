/**
 * Phase 11.1 — invitation accept-flow at `/invitations/[token]`.
 *
 * URL шейп выровнен с тем, что backend кладёт в email (Phase 4.11c notification-service):
 * `${PUBLIC_BASE_URL}/invitations/{token}`. Старый `/invite/[token]` (Phase 11.0)
 * остаётся для обратной совместимости с уже отправленными email'ами и не
 * затрагивается этим PR'ом.
 *
 * Server component делает pre-fetch ``GET /invitations/{token}`` (auth не
 * требуется — token = secret); выводы (404 / 410 / 200) пробрасываются в
 * client-component, чтобы не вертеть Clerk-state на сервере.
 */

import { InvitationAcceptClient, type LookupResult } from "./invitation-accept-client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

async function fetchLookup(token: string): Promise<LookupResult> {
  try {
    const res = await fetch(`${API_BASE}/invitations/${encodeURIComponent(token)}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (res.status === 404) {
      return { kind: "not_found" };
    }
    if (res.status === 410) {
      return { kind: "invalid" };
    }
    if (!res.ok) {
      return { kind: "error" };
    }
    const body = (await res.json()) as {
      invitee_email: string;
      role: "owner" | "editor" | "viewer";
      tree_id: string;
      tree_name: string;
      inviter_display_name: string;
      expires_at: string;
      accepted_at: string | null;
    };
    return { kind: "ok", data: body };
  } catch {
    return { kind: "error" };
  }
}

export default async function InvitationAcceptPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const lookup = await fetchLookup(token);
  return <InvitationAcceptClient token={token} lookup={lookup} />;
}
