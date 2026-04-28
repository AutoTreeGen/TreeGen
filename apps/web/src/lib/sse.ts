/**
 * Тонкий React-хук поверх native EventSource.
 *
 * Применение Phase 3.5 — подписка на ``GET /imports/{id}/events``: бэкенд
 * стримит JSON-фреймы прогресса импорта (см. ``ImportJobProgressEvent``).
 * Хук переподключается при сетевом обрыве (exponential backoff, до пяти
 * попыток) и сам закрывает соединение по терминальному событию (стадии
 * SUCCEEDED / FAILED / CANCELED).
 *
 * Сознательно не используется ``Last-Event-ID`` / replay: ADR-0026
 * фиксирует Phase 3.5 как простой pub/sub без re-replay (KISS).
 */

import { useEffect, useRef, useState } from "react";

// ---- Типы прогресс-события (зеркало backend ImportJobProgress) -------------

/**
 * Стадии импорта в верхнеуровневом порядке. Имена согласованы с
 * ``ImportRunner`` worker-PR (Phase 3.5 runner-PR). Терминальные стадии —
 * последние три; на них хук закрывает соединение.
 */
export type ImportStage =
  | "QUEUED"
  | "PARSING"
  | "ENTITIES"
  | "FAMILIES"
  | "EVENTS"
  | "SOURCES"
  | "FINALIZING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELED";

export const TERMINAL_STAGES: readonly ImportStage[] = ["SUCCEEDED", "FAILED", "CANCELED"];

export function isTerminalStage(stage: ImportStage): boolean {
  return TERMINAL_STAGES.includes(stage);
}

/**
 * Прогресс-кадр от backend SSE. Поля повторяют ``ImportJobProgress`` Pydantic-
 * схемы из parser-service. ``done`` / ``total`` — счётчики текущей стадии,
 * не суммарные. ``error`` — заполняется только при ``stage=FAILED``.
 */
export type ImportJobProgressEvent = {
  job_id: string;
  stage: ImportStage;
  done: number;
  total: number;
  message?: string | null;
  error?: string | null;
  /** ISO-8601 timestamp (UTC) — момент эмиссии события на воркере. */
  emitted_at: string;
};

// ---- Хук ------------------------------------------------------------------

const MAX_RETRIES = 5;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8_000;

export type UseEventSourceState<T> = {
  data: T | null;
  error: Error | null;
  isConnected: boolean;
  retries: number;
  close: () => void;
};

export type UseEventSourceOptions<T> = {
  /**
   * Признак терминального события — после него хук закрывает соединение
   * и больше не пытается переподключиться. По умолчанию срабатывает
   * только для ``ImportJobProgressEvent`` (см. ``isTerminalStage``).
   */
  isTerminal?: (event: T) => boolean;
  /**
   * Фабрика EventSource — переопределяется в тестах для подмены реального
   * браузерного объекта на заглушку.
   */
  eventSourceFactory?: (url: string) => EventSource;
};

/**
 * Подписка на SSE-эндпоинт. Если ``url`` равен null — соединение не
 * открывается (полезно когда job_id ещё не получен).
 *
 * Backoff: 500 ms → 1 s → 2 s → 4 s → 8 s. После пятой неудачной попытки
 * хук перестаёт переподключаться и оставляет последний error в state.
 */
export function useEventSource<T = ImportJobProgressEvent>(
  url: string | null,
  options: UseEventSourceOptions<T> = {},
): UseEventSourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [retries, setRetries] = useState(0);

  // Refs нужны, чтобы effect-замыкание не таскало устаревшие версии
  // фабрики/проверки терминальности и чтобы close() оставался стабильным.
  const sourceRef = useRef<EventSource | null>(null);
  const closedManuallyRef = useRef(false);
  const isTerminalRef = useRef(options.isTerminal);
  const factoryRef = useRef(options.eventSourceFactory);

  isTerminalRef.current = options.isTerminal;
  factoryRef.current = options.eventSourceFactory;

  useEffect(() => {
    if (!url) return;

    closedManuallyRef.current = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const cleanupSource = () => {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };

    const open = () => {
      const factory = factoryRef.current ?? ((u: string) => new EventSource(u));
      const es = factory(url);
      sourceRef.current = es;

      es.onopen = () => {
        attempt = 0;
        setRetries(0);
        setIsConnected(true);
        setError(null);
      };

      es.onmessage = (ev: MessageEvent) => {
        try {
          const parsed = JSON.parse(ev.data) as T;
          setData(parsed);

          const checker = isTerminalRef.current ?? defaultIsTerminal;
          if (checker(parsed)) {
            closedManuallyRef.current = true;
            cleanupSource();
            setIsConnected(false);
          }
        } catch (parseErr) {
          setError(parseErr instanceof Error ? parseErr : new Error("Failed to parse SSE payload"));
        }
      };

      es.onerror = () => {
        // Native EventSource сам решает, что делать дальше: ``readyState``
        // CLOSED означает терминальный обрыв (нашему backoff пора), CONNECTING —
        // браузер уже сам перезапускает (ничего не делаем).
        if (closedManuallyRef.current) return;
        if (es.readyState !== EventSource.CLOSED) return;

        cleanupSource();
        setIsConnected(false);

        if (attempt >= MAX_RETRIES) {
          setError(new Error(`SSE connection failed after ${MAX_RETRIES} retries`));
          return;
        }

        const delay = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
        attempt += 1;
        setRetries(attempt);
        retryTimer = setTimeout(() => {
          if (closedManuallyRef.current) return;
          open();
        }, delay);
      };
    };

    open();

    return () => {
      closedManuallyRef.current = true;
      if (retryTimer) clearTimeout(retryTimer);
      cleanupSource();
      setIsConnected(false);
    };
  }, [url]);

  const close = () => {
    closedManuallyRef.current = true;
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setIsConnected(false);
  };

  return { data, error, isConnected, retries, close };
}

function defaultIsTerminal<T>(event: T): boolean {
  if (event && typeof event === "object" && "stage" in event) {
    const stage = (event as { stage: unknown }).stage;
    return typeof stage === "string" && (TERMINAL_STAGES as readonly string[]).includes(stage);
  }
  return false;
}
