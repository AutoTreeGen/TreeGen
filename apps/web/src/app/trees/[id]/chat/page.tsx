"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ContextPanel } from "@/components/chat/ContextPanel";
import { MessageBubble, type MessageKind } from "@/components/chat/MessageBubble";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchPerson, fetchTreeOwnerPerson } from "@/lib/api";
import { type ChatReferencedPerson, streamChatTurn } from "@/lib/chat/api";

/**
 * Phase 10.7c — AI chat page.
 *
 * State machine:
 * - `idle`: input enabled, "send" submits next turn.
 * - `streaming`: assistant streams text deltas; input disabled until done|error.
 * - `error`: terminal LLM/network error; user can retype + retry.
 *
 * Session-id хранится в локальном state; при новом message без session_id
 * server создаёт новую сессию и шлёт `session_id` в первом SSE-кадре.
 * Page-level state не persist'ится через reload — Phase 10.7d добавит
 * sessions list / resume URL.
 */

type LocalMessage = {
  /** UUID server-side ChatMessage если уже persist'ит, иначе client-side temp. */
  id: string;
  kind: MessageKind;
  content: string;
  references: ChatReferencedPerson[];
  /** True для assistant-сообщения, которое сейчас стримится. */
  streaming?: boolean;
};

let _tempIdCounter = 0;
function makeTempId(): string {
  _tempIdCounter += 1;
  return `local-${_tempIdCounter}`;
}

export default function ChatPage() {
  const params = useParams<{ id: string }>();
  const treeId = params.id;
  const t = useTranslations("chat");

  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const ownerQuery = useQuery({
    queryKey: ["chat-owner-person", treeId],
    queryFn: () => fetchTreeOwnerPerson(treeId),
    enabled: !!treeId,
  });
  const ownerPersonId = ownerQuery.data?.owner_person_id ?? null;

  const personQuery = useQuery({
    queryKey: ["chat-anchor-person", ownerPersonId],
    queryFn: () => fetchPerson(ownerPersonId as string),
    enabled: !!ownerPersonId,
  });

  const anchorLabel = useMemo(() => {
    const detail = personQuery.data;
    if (!detail) return null;
    const primary = detail.names.find((n) => n.sort_order === 0) ?? detail.names[0];
    if (!primary) return null;
    return [primary.given_name, primary.surname].filter(Boolean).join(" ") || null;
  }, [personQuery.data]);

  // Aggregate уникальные refs из всех turn'ов — sidebar context panel.
  const allReferences = useMemo(() => {
    const seen = new Set<string>();
    const out: ChatReferencedPerson[] = [];
    for (const m of messages) {
      for (const r of m.references) {
        const key = `${r.person_id}-${r.mention_text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(r);
      }
    }
    return out;
  }, [messages]);

  // Auto-scroll to bottom on new content. Guarded для jsdom — там нет
  // scrollIntoView; production-браузеры всегда имеют.
  useEffect(() => {
    const el = messagesEndRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth" });
    }
  }, []);

  const onSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || streaming) return;

      setError(null);
      setInput("");

      const userMsg: LocalMessage = {
        id: makeTempId(),
        kind: "user",
        content: trimmed,
        references: [],
      };
      const assistantMsg: LocalMessage = {
        id: makeTempId(),
        kind: "assistant",
        content: "",
        references: [],
        streaming: true,
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      try {
        const stream = streamChatTurn(treeId, {
          session_id: sessionId,
          message: trimmed,
          anchor_person_id: ownerPersonId,
        });
        for await (const frame of stream) {
          if (frame.type === "session") {
            setSessionId(frame.session_id);
          } else if (frame.type === "token") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, content: m.content + frame.delta } : m,
              ),
            );
          } else if (frame.type === "done") {
            // Patch user-side с резолвленными references; assistant-side
            // получает Phase 10.7c пустой list (см. backend chat.py).
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id === userMsg.id) {
                  return { ...m, references: frame.referenced_persons };
                }
                if (m.id === assistantMsg.id) {
                  return { ...m, id: frame.message_id, streaming: false };
                }
                return m;
              }),
            );
          } else if (frame.type === "error") {
            setError(frame.detail);
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantMsg.id ? { ...m, streaming: false } : m)),
            );
            break;
          }
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Network error";
        setError(msg);
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMsg.id ? { ...m, streaming: false } : m)),
        );
      } finally {
        setStreaming(false);
      }
    },
    [input, streaming, treeId, sessionId, ownerPersonId],
  );

  const noAnchor = ownerQuery.isSuccess && ownerPersonId === null;

  return (
    <div className="flex h-[calc(100vh-4rem)] w-full" data-testid="chat-page">
      <main className="flex flex-1 flex-col">
        <header className="border-b border-gray-200 px-6 py-4">
          <h1 className="text-lg font-medium">{t("heading")}</h1>
          <p className="text-xs text-gray-500">{t("subtitle")}</p>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4" data-testid="chat-messages">
          {noAnchor && <MessageBubble kind="system" content={t("noAnchor")} />}
          {messages.length === 0 && !noAnchor && (
            <MessageBubble kind="system" content={t("emptyHint")} />
          )}
          {messages.map((m) => (
            <MessageBubble
              key={m.id}
              kind={m.kind}
              content={m.content}
              references={m.references}
              streaming={m.streaming}
            />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {error && (
          <div
            className="border-t border-red-200 bg-red-50 px-6 py-2 text-sm text-red-800"
            data-testid="chat-error"
            role="alert"
          >
            {error}
          </div>
        )}

        <form onSubmit={onSubmit} className="border-t border-gray-200 px-6 py-4 flex gap-2">
          <Input
            type="text"
            placeholder={noAnchor ? t("inputPlaceholderNoAnchor") : t("inputPlaceholder")}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={streaming || noAnchor}
            className="flex-1"
            data-testid="chat-input"
          />
          <Button
            type="submit"
            disabled={!input.trim() || streaming || noAnchor}
            data-testid="chat-send"
          >
            {streaming ? t("sending") : t("send")}
          </Button>
        </form>
      </main>

      <ContextPanel anchorLabel={anchorLabel} referencedPersons={allReferences} />
    </div>
  );
}
