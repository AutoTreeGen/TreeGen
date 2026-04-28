/**
 * User account settings API client (Phase 4.10b, ADR-0038).
 *
 * Тонкий wrapper над ``parser_service.api.users``-эндпоинтами:
 *
 * * ``GET /users/me`` — fetchMe()
 * * ``PATCH /users/me`` — updateMe(body)
 * * ``POST /users/me/erasure-request`` — requestErasure(body)
 * * ``POST /users/me/export-request`` — requestExport()
 * * ``GET /users/me/requests`` — fetchMyRequests()
 *
 * Все вызовы автоматически прикрепляют Bearer JWT через
 * ``setAuthTokenProvider``-singleton из ``./api``.
 */

import { ApiError } from "./api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// ---- Types (зеркало parser_service.api.users) -------------------------------

export type UserMe = {
  id: string;
  email: string;
  clerk_user_id: string | null;
  display_name: string | null;
  locale: string;
  timezone: string | null;
};

export type UserMeUpdate = {
  display_name?: string | null;
  locale?: string;
  timezone?: string | null;
};

export type ActionRequestKind = "export" | "erasure";
export type ActionRequestStatus = "pending" | "processing" | "done" | "failed" | "cancelled";

export type UserActionRequestItem = {
  id: string;
  kind: ActionRequestKind;
  status: ActionRequestStatus;
  created_at: string;
  processed_at: string | null;
  error: string | null;
  request_metadata: Record<string, unknown>;
};

export type ActionRequestCreated = {
  request_id: string;
  kind: ActionRequestKind;
  status: "pending";
};

// ---- Token wiring -----------------------------------------------------------
// Локальная копия ``authHeaders``-логики, чтобы не плодить зависимостей
// с api.ts: тот же singleton-getter, прочитанный через `getAuthToken`.

let authTokenProvider: (() => Promise<string | null>) | null = null;

export function setUserSettingsAuthTokenProvider(
  provider: (() => Promise<string | null>) | null,
): void {
  authTokenProvider = provider;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (!authTokenProvider) return {};
  const token = await authTokenProvider();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---- Fetch helpers ----------------------------------------------------------

async function callJson<T>(path: string, init: RequestInit & { method: string }): Promise<T> {
  const auth = await authHeaders();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...auth,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw new ApiError(
      response.status,
      detail ?? `Request to ${path} failed with ${response.status}`,
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function safeReadDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      // FastAPI 422 — массив ValidationError'ов; собираем msg-поля.
      const msgs = payload.detail
        .map((d: unknown) =>
          d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null,
        )
        .filter(Boolean);
      return msgs.length ? msgs.join("; ") : null;
    }
    return null;
  } catch {
    return null;
  }
}

// ---- Public surface ---------------------------------------------------------

export function fetchMe(): Promise<UserMe> {
  return callJson<UserMe>("/users/me", { method: "GET" });
}

export function updateMe(body: UserMeUpdate): Promise<UserMe> {
  return callJson<UserMe>("/users/me", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function requestErasure(body: { confirm_email: string }): Promise<ActionRequestCreated> {
  return callJson<ActionRequestCreated>("/users/me/erasure-request", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function requestExport(): Promise<ActionRequestCreated> {
  return callJson<ActionRequestCreated>("/users/me/export-request", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export type MyRequestsResponse = {
  user_id: string;
  items: UserActionRequestItem[];
};

export function fetchMyRequests(): Promise<MyRequestsResponse> {
  return callJson<MyRequestsResponse>("/users/me/requests", { method: "GET" });
}
