"use client";

import type { ChatReferencedPerson } from "@/lib/chat/api";

/**
 * Один turn в chat-окне: user / assistant / system.
 *
 * Layout: user-сообщения справа, assistant и system слева (классический
 * chat-рисунок). System используется для UI-only маркеров (приветствие,
 * предупреждение что anchor не задан) — не уходит в LLM, не persist'ится
 * с references.
 *
 * Prop назван ``kind`` (не ``role``) намеренно: Biome's ``useValidAriaRole``
 * проверяет JSX-attribute ``role`` и не различает custom-component vs HTML
 * element. ``kind`` обходит false-positive без noqa-suppress'ов.
 *
 * `references` — список разрешённых person-mention'ов (из 10.7b ego_resolver).
 * Phase 10.7c рендерит их как badge-цепочку под сообщением; future-work
 * (10.7d) сделает их кликабельными ссылками на person card.
 */

export type MessageKind = "user" | "assistant" | "system";

export type MessageBubbleProps = {
  kind: MessageKind;
  content: string;
  /** True пока стрим assistant-сообщения не завершился — рендерим caret. */
  streaming?: boolean;
  references?: ChatReferencedPerson[];
};

const BUBBLE_BASE = "max-w-[80%] rounded-lg px-4 py-3 text-sm whitespace-pre-wrap";

const KIND_STYLES: Record<MessageKind, string> = {
  user: "ml-auto bg-blue-600 text-white",
  assistant: "mr-auto bg-gray-100 text-gray-900 border border-gray-200",
  system: "mx-auto bg-yellow-50 text-yellow-900 border border-yellow-200 text-xs italic",
};

export function MessageBubble({
  kind,
  content,
  streaming = false,
  references = [],
}: MessageBubbleProps) {
  const wrapperJustify =
    kind === "user" ? "justify-end" : kind === "system" ? "justify-center" : "justify-start";
  return (
    <div className={`flex ${wrapperJustify}`} data-testid={`chat-bubble-${kind}`}>
      <div className={`${BUBBLE_BASE} ${KIND_STYLES[kind]}`}>
        <div>
          {content}
          {streaming && (
            <span
              aria-hidden="true"
              className="ml-0.5 inline-block w-2 h-4 bg-current opacity-60 animate-pulse align-text-bottom"
              data-testid="chat-bubble-caret"
            />
          )}
        </div>
        {references.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5" data-testid="chat-bubble-references">
            {references.map((ref) => (
              <span
                key={`${ref.person_id}-${ref.mention_text}`}
                className="inline-flex items-center gap-1 rounded-md bg-white/20 px-2 py-0.5 text-xs"
                title={`confidence: ${ref.confidence.toFixed(2)}`}
              >
                <span className="opacity-70">→</span>
                <span>{ref.mention_text}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
