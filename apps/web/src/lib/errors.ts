/**
 * Типизированная иерархия ошибок API-клиента (Phase 4.6, ADR-0041).
 *
 * Базовый ``ApiError`` остаётся для backward-compat: code, который
 * проверяет `instanceof ApiError`, продолжает работать. Конкретные
 * подклассы дают тонкое UX-различение:
 *
 * * ``NetworkError`` — fetch не дошёл до сервера (TypeError, abort,
 *   DNS, offline). Retry safe.
 * * ``AuthError`` — 401 / 403. ``AuthError(401)`` триггерит редирект
 *   на /sign-in (см. lib/api.ts). 403 — показываем «нет доступа», без
 *   редиректа.
 * * ``ValidationError`` — 4xx (кроме 401/403). User-input проблема, не
 *   ретраим, показываем `message` как-есть.
 * * ``ServerError`` — 5xx. Retry-safe (с exponential backoff).
 * * ``ApiError`` — fallback для всех остальных (3xx, неожиданные).
 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class NetworkError extends ApiError {
  constructor(message = "Network request failed") {
    super(0, message);
    this.name = "NetworkError";
  }
}

export class AuthError extends ApiError {
  constructor(status: 401 | 403, message: string) {
    super(status, message);
    this.name = "AuthError";
  }
}

export class ValidationError extends ApiError {
  constructor(status: number, message: string) {
    super(status, message);
    this.name = "ValidationError";
  }
}

export class ServerError extends ApiError {
  constructor(status: number, message: string) {
    super(status, message);
    this.name = "ServerError";
  }
}

/**
 * Pure-helper: классифицирует HTTP status в правильный subclass ``ApiError``.
 * 0 (или TypeError из fetch) — NetworkError; caller обрабатывает отдельно.
 */
export function classifyHttpError(status: number, message: string): ApiError {
  if (status === 401 || status === 403) {
    return new AuthError(status, message);
  }
  if (status >= 400 && status < 500) {
    return new ValidationError(status, message);
  }
  if (status >= 500) {
    return new ServerError(status, message);
  }
  return new ApiError(status, message);
}

/** True если ошибку имеет смысл повторить через backoff. */
export function isRetryableError(err: unknown): boolean {
  return err instanceof NetworkError || err instanceof ServerError;
}
