/**
 * Chat API client (Phase 10.7c).
 *
 * `POST /trees/{treeId}/chat/turn` стримит SSE-кадры в response body.
 * Браузерный `EventSource` поддерживает только GET, поэтому используем
 * `fetch` с `ReadableStream` и парсим SSE-формат руками — формат простой:
 * блок `data: <json>\n\n` на кадр.
 *
 * Server-side кадры:
 * - `{ type: "session", session_id, anchor_person_id }` — первый;
 * - `{ type: "token", delta }` — text-deltas Claude'а;
 * - `{ type: "done", message_id, referenced_persons }` — финальный success;
 * - `{ type: "error", detail }` — terminal error.
 */

import { getAuthHeaders } from "../api";
import { ApiError, classifyHttpError } from "../errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// ---- Types ------------------------------------------------------------------

export type ChatTurnRequest = {
  session_id: string | null;
  message: string;
  anchor_person_id: string | null;
};

export type ChatReferencedPerson = {
  person_id: string;
  mention_text: string;
  confidence: number;
};

export type ChatSessionFrame = {
  type: "session";
  session_id: string;
  anchor_person_id: string;
};

export type ChatTokenFrame = {
  type: "token";
  delta: string;
};

export type ChatDoneFrame = {
  type: "done";
  message_id: string;
  referenced_persons: ChatReferencedPerson[];
};

export type ChatErrorFrame = {
  type: "error";
  detail: string;
};

export type ChatFrame = ChatSessionFrame | ChatTokenFrame | ChatDoneFrame | ChatErrorFrame;

// ---- Streaming helper -------------------------------------------------------

/**
 * Hook для тестов: подменяет глобальный fetch без monkey-patch'а window.
 * Mirrors lib/api.ts setFetchImpl.
 */
let _fetchImpl: typeof fetch = (...args) => fetch(...args);

export function setChatFetchImpl(impl: typeof fetch): void {
  _fetchImpl = impl;
}

/**
 * Async-генератор кадров одного chat turn'а.
 *
 * Caller итерирует через `for await`; каждый yield — один parsed frame.
 * Поток терминируется естественно (server закрывает соединение после
 * `done` или `error`), либо caller может прервать через `AbortController`.
 */
export async function* streamChatTurn(
  treeId: string,
  body: ChatTurnRequest,
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<ChatFrame, void, void> {
  const headers = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...(await getAuthHeaders()),
  };

  const response = await _fetchImpl(`${API_BASE}/trees/${treeId}/chat/turn`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    throw classifyHttpError(response.status, text || `Chat turn failed with ${response.status}`);
  }
  if (!response.body) {
    throw new ApiError(500, "Chat response had no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE: кадры разделены `\n\n`. Может прийти 0+ полных кадров +
      // незавершённый хвост — храним хвост в `buffer`.
      const segments = buffer.split(/\r?\n\r?\n/);
      buffer = segments.pop() ?? "";
      for (const segment of segments) {
        const frame = parseSseFrame(segment);
        if (frame !== null) yield frame;
      }
    }
    // Финальный хвост (если сервер не отправил trailing \n\n).
    if (buffer.trim()) {
      const frame = parseSseFrame(buffer);
      if (frame !== null) yield frame;
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

function parseSseFrame(segment: string): ChatFrame | null {
  // Стандартный SSE может содержать `event:`, `id:`, `retry:`, `:comment`.
  // Phase 10.7c сервер шлёт только `data: <json>` + опционально `:ping`
  // (heartbeat от sse-starlette). Берём только `data:`-строки и склеиваем.
  const dataLines: string[] = [];
  for (const rawLine of segment.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  try {
    return JSON.parse(dataLines.join("\n")) as ChatFrame;
  } catch {
    return null;
  }
}
