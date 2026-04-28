/**
 * Exponential backoff retry для API-вызовов (Phase 4.6, ADR-0041).
 *
 * Контракт:
 *
 * * ``maxAttempts`` — общее число попыток (включая первую). Default 3.
 * * Backoff: 200ms, 400ms, 800ms, 1.6s, ... + jitter ±25%.
 * * Retry'ится только если ``shouldRetry(err)`` возвращает true.
 *   Default: ``isRetryableError`` (NetworkError / ServerError 5xx).
 * * При исчерпании попыток re-throw'ает последний error.
 */

import { isRetryableError } from "./errors";

export type RetryOptions = {
  /** Максимум попыток включая первую. Default 3. */
  maxAttempts?: number;
  /** Стартовая задержка в мс. Default 200. */
  baseDelayMs?: number;
  /** Решает, повторять ли при данной ошибке. Default isRetryableError. */
  shouldRetry?: (err: unknown) => boolean;
  /** Hook для тестов: подменяет setTimeout. */
  sleep?: (ms: number) => Promise<void>;
};

const _defaultSleep = (ms: number): Promise<void> =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

export async function withRetry<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 3;
  const baseDelayMs = options.baseDelayMs ?? 200;
  const shouldRetry = options.shouldRetry ?? isRetryableError;
  const sleep = options.sleep ?? _defaultSleep;

  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt >= maxAttempts || !shouldRetry(err)) {
        throw err;
      }
      // Экспоненциальный backoff: 200, 400, 800, ... с jitter 75–125%.
      const exp = baseDelayMs * 2 ** (attempt - 1);
      const jitter = exp * (0.75 + Math.random() * 0.5);
      await sleep(jitter);
    }
  }
  // Unreachable — цикл либо вернёт результат, либо бросит.
  throw lastError;
}
