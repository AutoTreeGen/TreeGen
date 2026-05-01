/**
 * Phase 10.9d — Типизированный клиент к voice-to-tree эндпоинтам
 * parser-service'а.
 *
 * Зеркалит Pydantic-схемы из ``services/parser-service/src/parser_service/schemas.py``
 * (``AudioConsent*``, ``AudioSession*``). При изменении контракта в backend'е
 * — обновить здесь руками; OpenAPI-codegen появится в Phase 4.2.
 *
 * Маршруты (см. ``services/parser-service/src/parser_service/api/audio_*``):
 *   GET    /trees/{tree_id}/audio-consent
 *   POST   /trees/{tree_id}/audio-consent       (owner-only)
 *   DELETE /trees/{tree_id}/audio-consent       (owner-only, 202 Accepted)
 *   GET    /trees/{tree_id}/audio-sessions      (paginated)
 *   POST   /trees/{tree_id}/audio-sessions      (multipart, editor)
 *   GET    /audio-sessions/{session_id}
 *   DELETE /audio-sessions/{session_id}         (soft-delete)
 */

import { ApiError, classifyHttpError } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// ---- Types (зеркало parser_service.schemas) --------------------------------

export type AudioConsentProvider = "openai" | "self-hosted-whisper";

export type AudioSessionStatus = "uploaded" | "transcribing" | "ready" | "failed";

export type AudioConsentResponse = {
  tree_id: string;
  audio_consent_egress_at: string | null;
  audio_consent_egress_provider: AudioConsentProvider | null;
};

export type AudioConsentRevokeResponse = {
  tree_id: string;
  revoked_at: string;
  enqueued_session_ids: string[];
};

export type AudioSessionResponse = {
  id: string;
  tree_id: string;
  status: AudioSessionStatus;
  storage_uri: string;
  mime_type: string;
  duration_sec: number | null;
  size_bytes: number;
  language: string | null;
  transcript_text: string | null;
  transcript_provider: string | null;
  transcript_model_version: string | null;
  /**
   * Decimal в Python → строка в JSON (FastAPI default-сериализатор).
   * Парсим в Number на месте отображения, не теряя 4-decimal точности.
   */
  transcript_cost_usd: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type AudioSessionListResponse = {
  tree_id: string;
  total: number;
  page: number;
  per_page: number;
  items: AudioSessionResponse[];
};

// ---- Auth + fetch wiring (mirror of lib/api.ts) -----------------------------

let _fetchImpl: typeof fetch = (...args) => fetch(...args);
export function setFetchImpl(impl: typeof fetch): void {
  _fetchImpl = impl;
}

type AuthTokenProvider = () => Promise<string | null>;
let authTokenProvider: AuthTokenProvider | null = null;
export function setVoiceAuthTokenProvider(provider: AuthTokenProvider | null): void {
  authTokenProvider = provider;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (authTokenProvider === null) return {};
  const token = await authTokenProvider();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function safeReadDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail && typeof payload.detail === "object") {
      // FastAPI HTTPException(detail={"error_code": ..., "message": ...}).
      const obj = payload.detail as { message?: unknown; error_code?: unknown };
      if (typeof obj.message === "string") return obj.message;
      if (typeof obj.error_code === "string") return obj.error_code;
    }
    return null;
  } catch {
    return null;
  }
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const auth = await authHeaders();
  const response = await _fetchImpl(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body && !(init.body instanceof FormData) && !init?.headers
        ? { "Content-Type": "application/json" }
        : {}),
      ...auth,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw classifyHttpError(
      response.status,
      detail ?? `Request to ${path} failed with ${response.status}`,
    );
  }
  return (await response.json()) as T;
}

// ---- Public API: consent ----------------------------------------------------

export function fetchAudioConsent(treeId: string): Promise<AudioConsentResponse> {
  return jsonRequest<AudioConsentResponse>(`/trees/${treeId}/audio-consent`);
}

export function grantAudioConsent(
  treeId: string,
  provider: AudioConsentProvider = "openai",
): Promise<AudioConsentResponse> {
  return jsonRequest<AudioConsentResponse>(`/trees/${treeId}/audio-consent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
}

export function revokeAudioConsent(treeId: string): Promise<AudioConsentRevokeResponse> {
  return jsonRequest<AudioConsentRevokeResponse>(`/trees/${treeId}/audio-consent`, {
    method: "DELETE",
  });
}

// ---- Public API: sessions ---------------------------------------------------

export type ListAudioSessionsParams = {
  page?: number;
  perPage?: number;
};

export function fetchAudioSessions(
  treeId: string,
  { page = 1, perPage = 20 }: ListAudioSessionsParams = {},
): Promise<AudioSessionListResponse> {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  return jsonRequest<AudioSessionListResponse>(
    `/trees/${treeId}/audio-sessions?${params.toString()}`,
  );
}

export function fetchAudioSession(sessionId: string): Promise<AudioSessionResponse> {
  return jsonRequest<AudioSessionResponse>(`/audio-sessions/${sessionId}`);
}

export function softDeleteAudioSession(sessionId: string): Promise<AudioSessionResponse> {
  return jsonRequest<AudioSessionResponse>(`/audio-sessions/${sessionId}`, { method: "DELETE" });
}

/**
 * Multipart-upload Blob'а из MediaRecorder'а. Не используем ``jsonRequest``
 * (он тащит ``Accept: application/json`` без ``Content-Type``, для FormData
 * браузер должен ставить multipart-boundary сам).
 */
export async function uploadAudioSession(
  treeId: string,
  audio: Blob,
  options?: { languageHint?: string; filename?: string },
): Promise<AudioSessionResponse> {
  const body = new FormData();
  // Filename — для UX логов на сервере; реальный mime / size читаем из blob.
  const filename = options?.filename ?? `recording-${Date.now()}.webm`;
  body.append("audio", audio, filename);
  if (options?.languageHint && options.languageHint.length > 0) {
    body.append("language_hint", options.languageHint);
  }
  const auth = await authHeaders();
  const response = await _fetchImpl(`${API_BASE}/trees/${treeId}/audio-sessions`, {
    method: "POST",
    body,
    headers: { Accept: "application/json", ...auth },
  });
  if (!response.ok) {
    const detail = await safeReadDetail(response);
    throw classifyHttpError(
      response.status,
      detail ?? `Audio upload failed with ${response.status}`,
    );
  }
  return (await response.json()) as AudioSessionResponse;
}

// Re-export типизированных ошибок для консьюмеров — backward-compat пара
// с lib/api.ts.
export { ApiError };
