"use client";

import { useTranslations } from "next-intl";

import type { ChatReferencedPerson } from "@/lib/chat/api";

/**
 * Боковая панель chat-страницы: показывает self-anchor дерева и список
 * персон, упомянутых ассистентом / резолвленных в user-сообщении.
 *
 * Phase 10.7c рендерит read-only labels; 10.7d сделает person-id'ы
 * кликабельными ссылками на person card.
 */

export type ContextPanelProps = {
  /** Label anchor-персоны (e.g. "Vladimir Z") или null если anchor не задан. */
  anchorLabel: string | null;
  /** Уникальные referenced persons из всех turns'ов сессии. */
  referencedPersons: ChatReferencedPerson[];
};

export function ContextPanel({ anchorLabel, referencedPersons }: ContextPanelProps) {
  const t = useTranslations("chat.context");
  return (
    <aside
      className="w-64 shrink-0 border-l border-gray-200 bg-gray-50 p-4 text-sm"
      data-testid="chat-context-panel"
    >
      <section>
        <h3 className="mb-2 font-medium text-gray-700">{t("anchor")}</h3>
        {anchorLabel ? (
          <div
            className="rounded-md bg-white px-3 py-2 text-gray-900 border border-gray-200"
            data-testid="chat-context-anchor"
          >
            {anchorLabel}
          </div>
        ) : (
          <div className="rounded-md bg-yellow-50 px-3 py-2 text-yellow-900 border border-yellow-200 text-xs italic">
            {t("anchorMissing")}
          </div>
        )}
      </section>
      <section className="mt-6">
        <h3 className="mb-2 font-medium text-gray-700">{t("people")}</h3>
        {referencedPersons.length === 0 ? (
          <p className="text-xs text-gray-500 italic" data-testid="chat-context-people-empty">
            {t("peopleEmpty")}
          </p>
        ) : (
          <ul className="space-y-1" data-testid="chat-context-people-list">
            {referencedPersons.map((ref) => (
              <li
                key={`${ref.person_id}-${ref.mention_text}`}
                className="flex items-center justify-between rounded bg-white px-2 py-1 border border-gray-200"
              >
                <span className="truncate text-gray-900">{ref.mention_text}</span>
                <span
                  className="ml-2 shrink-0 text-xs text-gray-500"
                  title={`confidence: ${ref.confidence.toFixed(2)}`}
                >
                  {Math.round(ref.confidence * 100)}%
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
