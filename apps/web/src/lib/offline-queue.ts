/**
 * Offline action queue (Phase 4.6, ADR-0041 — STUB).
 *
 * Phase 4.6 ships только enqueue/list — actual retry-flush делается
 * Phase 4.7 после того, как настоящие mutating endpoint'ы (создание
 * person, edit fact) появятся в UI. Сейчас в UI только read-flow,
 * поэтому очередь редко bisited; держим её mocked-by-default
 * чтобы not-yet-installed ``idb-keyval`` или SSR'ный путь не падал.
 *
 * Каждый enqueued item — JSON-сериализуемый payload, который позже
 * будет re-issued через ``fetchOnceJson(...)``. Никаких promise'ов
 * мы не сериализуем — только path + method + body.
 */

import { del, get, set } from "idb-keyval";

const QUEUE_KEY = "autotreegen.offline_queue.v1";

export type QueuedAction = {
  id: string;
  /** Относительный API path. */
  path: string;
  /** "POST" / "PATCH" / "DELETE" — GET'ы не имеет смысла очередить. */
  method: "POST" | "PATCH" | "DELETE";
  /** JSON-сериализуемое body. */
  body: unknown;
  /** ISO 8601 timestamp создания. */
  enqueuedAt: string;
};

export async function enqueue(
  action: Omit<QueuedAction, "id" | "enqueuedAt">,
): Promise<QueuedAction> {
  const queue = await listQueue();
  const item: QueuedAction = {
    ...action,
    id: crypto.randomUUID(),
    enqueuedAt: new Date().toISOString(),
  };
  await set(QUEUE_KEY, [...queue, item]);
  return item;
}

export async function listQueue(): Promise<QueuedAction[]> {
  const stored = (await get(QUEUE_KEY)) as QueuedAction[] | undefined;
  return stored ?? [];
}

export async function clearQueue(): Promise<void> {
  await del(QUEUE_KEY);
}
