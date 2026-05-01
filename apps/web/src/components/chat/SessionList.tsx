"use client";

import { useTranslations } from "next-intl";

import type { ChatSessionListItem } from "@/lib/chat/api";

/**
 * Phase 10.7d — sidebar list чат-сессий пользователя в дереве.
 *
 * Click на item меняет URL (`/trees/[id]/chat?session=<uuid>`) — родительский
 * page reads search-param и грузит историю через `loadChatMessages`.
 * "New chat" сбрасывает session_id (link на `/trees/[id]/chat` без query).
 */

export type SessionListProps = {
  sessions: ChatSessionListItem[];
  /** UUID активной сессии (для подсветки selected-state). */
  activeSessionId: string | null;
  /** Callback на click — caller обновляет URL через next/navigation router. */
  onSelectSession: (sessionId: string) => void;
  /** Callback на "New chat" — caller сбрасывает session state. */
  onNewSession: () => void;
  /** Loading flag — показываем skeleton/spinner. */
  loading?: boolean;
};

function _formatTitle(session: ChatSessionListItem, untitledLabel: string): string {
  if (session.title?.trim()) {
    return session.title;
  }
  return untitledLabel;
}

function _formatTimestamp(iso: string | null): string {
  if (!iso) return "";
  // Не зависим от toLocaleString locale runtime'а — relative time добавим
  // позже если понадобится; пока stable «YYYY-MM-DD HH:MM» формат.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

export function SessionList({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  loading = false,
}: SessionListProps) {
  const t = useTranslations("chat.sessions");
  return (
    <aside
      className="w-64 shrink-0 border-r border-gray-200 bg-white p-3 text-sm flex flex-col"
      data-testid="chat-session-list"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-medium text-gray-700">{t("heading")}</h3>
        <button
          type="button"
          onClick={onNewSession}
          className="text-xs text-blue-600 hover:underline"
          data-testid="chat-new-session"
        >
          {t("newChat")}
        </button>
      </div>
      {loading ? (
        <p className="text-xs text-gray-400 italic" data-testid="chat-session-list-loading">
          {t("loading")}
        </p>
      ) : sessions.length === 0 ? (
        <p className="text-xs text-gray-500 italic" data-testid="chat-session-list-empty">
          {t("empty")}
        </p>
      ) : (
        <ul className="space-y-1 overflow-y-auto" data-testid="chat-session-list-items">
          {sessions.map((s) => {
            const isActive = s.id === activeSessionId;
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => onSelectSession(s.id)}
                  className={`w-full text-left rounded px-2 py-1.5 border transition-colors ${
                    isActive
                      ? "bg-blue-50 border-blue-300 text-blue-900"
                      : "bg-white border-gray-200 hover:bg-gray-50 text-gray-900"
                  }`}
                  data-testid={`chat-session-${s.id}`}
                  data-active={isActive ? "true" : "false"}
                >
                  <div className="truncate font-medium">{_formatTitle(s, t("untitled"))}</div>
                  <div className="flex items-center justify-between mt-0.5 text-[11px] text-gray-500">
                    <span>{_formatTimestamp(s.last_message_at ?? s.created_at)}</span>
                    <span>{t("messageCount", { count: s.message_count })}</span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
