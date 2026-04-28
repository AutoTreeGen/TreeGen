/**
 * Типизированный fetch-клиент к notification-service (Phase 8.0).
 *
 * Зеркалит ``services/notification-service/src/notification_service/schemas.py``.
 * Phase 4.2 заменит ручной клиент на OpenAPI-codegen, единый для всех сервисов.
 *
 * Auth: пока mock через ``X-User-Id`` header. Phase 4.x подменит на JWT —
 * меняется одно место (DEFAULT_USER_ID + addAuthHeaders).
 */

const NOTIFICATION_API_BASE =
  process.env.NEXT_PUBLIC_NOTIFICATION_API_URL?.replace(/\/$/, "") ?? "http://localhost:8002";

/**
 * Demo user для mock-auth до появления реального login flow (Phase 4.x).
 * Совпадает с user_id, который parser-service использует для ``UUID.int``.
 *
 * При реальном auth — читать из session/cookie/JWT, передавать сюда.
 */
const DEFAULT_USER_ID = process.env.NEXT_PUBLIC_DEMO_USER_ID ?? "1";

// ---- Types (зеркало notification_service.schemas) ---------------------------

export type NotificationSummary = {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  delivered_at: string | null;
  read_at: string | null;
  created_at: string;
};

export type NotificationListResponse = {
  user_id: number;
  total: number;
  unread: number;
  limit: number;
  offset: number;
  items: NotificationSummary[];
};

export type MarkReadResponse = {
  id: string;
  read_at: string;
};

export type PreferenceItem = {
  event_type: string;
  enabled: boolean;
  channels: string[];
  is_default: boolean;
};

export type PreferenceListResponse = {
  user_id: number;
  items: PreferenceItem[];
};

export type PreferenceUpdateResponse = {
  user_id: number;
  event_type: string;
  enabled: boolean;
  channels: string[];
};

// ---- HTTP error -------------------------------------------------------------

export class NotificationApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "NotificationApiError";
    this.status = status;
  }
}

function authHeaders(userId: string): Record<string, string> {
  // Mock auth: header только. JWT-замена изменит одно это место.
  return { "X-User-Id": userId };
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  userId: string = DEFAULT_USER_ID,
): Promise<T> {
  const response = await fetch(`${NOTIFICATION_API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...authHeaders(userId),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new NotificationApiError(
      response.status,
      `Request to ${path} failed with ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as T;
}

// ---- Public surface ---------------------------------------------------------

export function fetchNotifications(
  options: { unread?: boolean; limit?: number; offset?: number } = {},
  userId: string = DEFAULT_USER_ID,
): Promise<NotificationListResponse> {
  const { unread = false, limit = 20, offset = 0 } = options;
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (unread) params.set("unread", "true");
  return request<NotificationListResponse>(
    `/users/me/notifications?${params.toString()}`,
    {},
    userId,
  );
}

export function markNotificationRead(
  notificationId: string,
  userId: string = DEFAULT_USER_ID,
): Promise<MarkReadResponse> {
  return request<MarkReadResponse>(
    `/notifications/${notificationId}/read`,
    { method: "PATCH" },
    userId,
  );
}

export function fetchPreferences(
  userId: string = DEFAULT_USER_ID,
): Promise<PreferenceListResponse> {
  return request<PreferenceListResponse>("/users/me/notification-preferences", {}, userId);
}

export function updatePreference(
  eventType: string,
  body: { enabled?: boolean; channels?: string[] },
  userId: string = DEFAULT_USER_ID,
): Promise<PreferenceUpdateResponse> {
  return request<PreferenceUpdateResponse>(
    `/users/me/notification-preferences/${encodeURIComponent(eventType)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    userId,
  );
}

/**
 * Deep-link для нотификации в зависимости от ``event_type``.
 *
 * Возвращает ``null`` для типов, у которых ещё нет UI-страницы — UI
 * отрисует их как «неактивные» элементы dropdown'а без перехода.
 */
export function notificationDeepLink(notification: NotificationSummary): string | null {
  switch (notification.event_type) {
    case "hypothesis_pending_review": {
      const hid = notification.payload.hypothesis_id;
      return typeof hid === "string" ? `/hypotheses/${hid}` : null;
    }
    case "import_completed":
    case "import_failed": {
      const tid = notification.payload.tree_id;
      return typeof tid === "string" ? `/trees/${tid}/persons` : null;
    }
    case "dedup_suggestion_new": {
      const tid = notification.payload.tree_id;
      return typeof tid === "string" ? `/trees/${tid}/duplicates` : null;
    }
    default:
      return null;
  }
}
