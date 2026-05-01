"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ContextPanel } from "@/components/chat/ContextPanel";
import { MessageBubble, type MessageKind } from "@/components/chat/MessageBubble";
import { SessionList } from "@/components/chat/SessionList";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchPerson, fetchTreeOwnerPerson } from "@/lib/api";
import {
  type ChatReference,
  type ChatReferencedPerson,
  listChatSessions,
  loadChatMessages,
  streamChatTurn,
} from "@/lib/chat/api";

/**
 * Phase 10.7c — AI chat page; Phase 10.7d adds sessions list + resume URL +
 * history loading on mount.
 *
 * State machine:
 * - `idle`: input enabled, "send" submits next turn.
 * - `streaming`: assistant streams text deltas; input disabled until done|error.
 * - `error`: terminal LLM/network error; user can retype + retry.
 *
 * URL convention: `/trees/[id]/chat` = новая сессия; `/trees/[id]/chat?session=<uuid>`
 * = resume существующей. Server-side `session`-кадр фиксирует UUID в URL
 * после первого turn'а.
 */

function _refsAsPersonRefs(refs: ChatReference[]): ChatReferencedPerson[] {
  // ContextPanel рендерит только person-references; source-citations
  // отдельная секция (future-work для UI).
  const out: ChatReferencedPerson[] = [];
  for (const r of refs) {
    if (r.kind === "source") continue;
    out.push(r as ChatReferencedPerson);
  }
  return out;
}

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
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const sessionFromUrl = searchParams.get("session");

  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(sessionFromUrl ?? null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const ownerQuery = useQuery({
    queryKey: ["chat-owner-person", treeId],
    queryFn: () => fetchTreeOwnerPerson(treeId),
    enabled: !!treeId,
  });
  const ownerPersonId = ownerQuery.data?.owner_person_id ?? null;

  // Phase 10.7d — sessions sidebar.
  const sessionsQuery = useQuery({
    queryKey: ["chat-sessions", treeId],
    queryFn: () => listChatSessions(treeId, { limit: 30 }),
    enabled: !!treeId,
  });

  // Phase 10.7d — load history когда sessionId приходит из URL.
  const historyQuery = useQuery({
    queryKey: ["chat-history", treeId, sessionFromUrl],
    queryFn: () => loadChatMessages(treeId, sessionFromUrl as string, { limit: 200 }),
    enabled: !!treeId && !!sessionFromUrl,
  });

  // Hydrate `messages` state из history-query'а ровно один раз на смену
  // sessionFromUrl'а. После hydrate'а user'овский input и SSE-стрим живут
  // как раньше — мы не делаем messages контролируемым historyQuery.data'ой.
  const hydratedSessionRef = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionFromUrl) {
      hydratedSessionRef.current = null;
      return;
    }
    if (hydratedSessionRef.current === sessionFromUrl) return;
    if (!historyQuery.data) return;
    hydratedSessionRef.current = sessionFromUrl;
    setSessionId(sessionFromUrl);
    setMessages(
      historyQuery.data.items.map((item) => ({
        id: item.id,
        kind: item.role,
        content: item.content,
        references: _refsAsPersonRefs(item.references ?? []),
      })),
    );
  }, [sessionFromUrl, historyQuery.data]);

  // "New chat" — сбрасываем state и URL.
  const onNewSession = useCallback(() => {
    setMessages([]);
    setInput("");
    setSessionId(null);
    setError(null);
    hydratedSessionRef.current = null;
    router.replace(pathname);
  }, [pathname, router]);

  // Click на session item: переходим на ?session=<uuid>.
  const onSelectSession = useCallback(
    (sid: string) => {
      if (sid === sessionFromUrl) return;
      setMessages([]);
      setInput("");
      setSessionId(sid);
      setError(null);
      hydratedSessionRef.current = null;
      const params = new URLSearchParams();
      params.set("session", sid);
      router.replace(`${pathname}?${params.toString()}`);
    },
    [pathname, router, sessionFromUrl],
  );

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
            // Phase 10.7d — фиксируем session_id в URL чтобы reload/share
            // resume'ил discussion. router.replace без push'а в history.
            if (frame.session_id !== sessionFromUrl) {
              const params = new URLSearchParams();
              params.set("session", frame.session_id);
              router.replace(`${pathname}?${params.toString()}`);
              hydratedSessionRef.current = frame.session_id;
            }
          } else if (frame.type === "token") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, content: m.content + frame.delta } : m,
              ),
            );
          } else if (frame.type === "done") {
            // Patch user-side с резолвленными references; assistant-side
            // получает 10.7d-резолвленные refs (person + source).
            const userPersonRefs = _refsAsPersonRefs(frame.referenced_persons);
            const assistantPersonRefs = _refsAsPersonRefs(frame.assistant_references ?? []);
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id === userMsg.id) {
                  return { ...m, references: userPersonRefs };
                }
                if (m.id === assistantMsg.id) {
                  return {
                    ...m,
                    id: frame.message_id,
                    streaming: false,
                    references: assistantPersonRefs,
                  };
                }
                return m;
              }),
            );
            // Refresh sessions sidebar — title/aggregates меняются после first turn'а.
            sessionsQuery.refetch().catch(() => {});
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
    [
      input,
      streaming,
      treeId,
      sessionId,
      ownerPersonId,
      pathname,
      router,
      sessionFromUrl,
      sessionsQuery,
    ],
  );

  const noAnchor = ownerQuery.isSuccess && ownerPersonId === null;

  return (
    <div className="flex h-[calc(100vh-4rem)] w-full" data-testid="chat-page">
      <SessionList
        sessions={sessionsQuery.data?.items ?? []}
        activeSessionId={sessionId}
        loading={sessionsQuery.isLoading}
        onSelectSession={onSelectSession}
        onNewSession={onNewSession}
      />

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
