/**
 * Типизированный fetch-клиент к billing-service (Phase 12.0).
 *
 * Зеркалит ``services/billing-service/src/billing_service/schemas.py``.
 * При изменении Pydantic-схем — обновлять руками. Phase 4.2 заменит
 * ручной клиент на OpenAPI-codegen.
 */

const BILLING_API_BASE =
  process.env.NEXT_PUBLIC_BILLING_API_URL?.replace(/\/$/, "") ?? "http://localhost:8003";

// ---- Types -----------------------------------------------------------------

export type Plan = "free" | "pro";
export type SubscriptionStatus = "active" | "past_due" | "canceled" | "incomplete";

export type PlanLimits = {
  max_trees: number | null;
  max_persons_per_tree: number | null;
  dna_enabled: boolean;
  fs_import_enabled: boolean;
};

export type CurrentPlanResponse = {
  plan: Plan;
  status: SubscriptionStatus | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  limits: PlanLimits;
};

export type CheckoutResponse = {
  checkout_url: string;
  session_id: string;
};

export type PortalResponse = {
  portal_url: string;
};

/**
 * Структурированный 402 Payment Required от любого entitlement-gated
 * endpoint'а. Frontend ловит и показывает upgrade-modal со ссылкой на
 * ``upgrade_url``.
 */
export type PaymentRequiredDetail = {
  error: "payment_required";
  feature: "import_quota" | "fs_import_enabled" | "dna_enabled";
  current_plan: Plan;
  upgrade_url: string;
  message: string;
};

export class BillingApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "BillingApiError";
    this.status = status;
  }
}

// ---- Mock auth (X-User-Id header) ------------------------------------------

/**
 * Phase 12.0 mock auth: фронт читает user_id из localStorage. Phase 4.10
 * заменит на Clerk JWT. Имя ключа specifc для biling-debug режима, чтобы
 * случайный production-юзер не попал в state.
 */
function getMockUserId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("autotreegen.mock_user_id");
}

function authHeaders(): HeadersInit {
  const uid = getMockUserId();
  return uid ? { "X-User-Id": uid } : {};
}

// ---- HTTP helper -----------------------------------------------------------

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BILLING_API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail) detail = JSON.stringify(payload.detail);
    } catch {
      // ignore — пустой body
    }
    throw new BillingApiError(
      response.status,
      detail ?? `Request to ${path} failed with ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as T;
}

// ---- Public surface --------------------------------------------------------

export function fetchCurrentPlan(): Promise<CurrentPlanResponse> {
  return getJson<CurrentPlanResponse>("/billing/me");
}

export function startCheckout(plan: "pro"): Promise<CheckoutResponse> {
  return getJson<CheckoutResponse>("/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  });
}

export function openPortal(): Promise<PortalResponse> {
  return getJson<PortalResponse>("/billing/portal");
}
