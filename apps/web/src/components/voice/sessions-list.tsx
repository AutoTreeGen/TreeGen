"use client";

/**
 * Phase 10.9d — список audio sessions дерева.
 *
 * GET /trees/{id}/audio-sessions с polling'ом каждые 3 сек, ПОКА в выборке
 * есть session со status'ом ``uploaded`` или ``transcribing``. Когда все
 * ready/failed — polling выключаем (см. подводный камень №2 в брифе:
 * не делать 200 req/min на странице с десятком сессий).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type AudioSessionResponse,
  fetchAudioSessions,
  softDeleteAudioSession,
} from "@/lib/voice-api";

import { TranscriptViewer } from "./transcript-viewer";

const POLL_INTERVAL_MS = 3000;

function statusVariant(status: AudioSessionResponse["status"]): "neutral" | "accent" {
  return status === "ready" ? "accent" : "neutral";
}

export type SessionsListProps = {
  treeId: string;
};

export function SessionsList({ treeId }: SessionsListProps) {
  const t = useTranslations("voice.sessions");
  const tStatus = useTranslations("voice.sessions.status");
  const queryClient = useQueryClient();
  const [openSessionId, setOpenSessionId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: ["audio-sessions", treeId],
    queryFn: () => fetchAudioSessions(treeId, { perPage: 50 }),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const hasInFlight = items.some((s) => s.status === "uploaded" || s.status === "transcribing");
      return hasInFlight ? POLL_INTERVAL_MS : false;
    },
    // staleTime=0 — после upload'а blob создаёт row со status=uploaded;
    // мы сразу invalidate'имся с recorder'а, но если staleTime > 0 — кеш
    // не перечитается до его истечения, и пользователь не увидит новую запись.
    staleTime: 0,
  });

  const remove = useMutation({
    mutationFn: (sessionId: string) => softDeleteAudioSession(sessionId),
    onSuccess: () => {
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: ["audio-sessions", treeId] });
    },
    onError: (err) => {
      setDeleteError(err instanceof Error ? err.message : t("deleteFailed"));
    },
  });

  const onDelete = (sessionId: string) => {
    if (typeof window === "undefined" || window.confirm(t("deleteConfirm"))) {
      remove.mutate(sessionId);
    }
  };

  return (
    <Card data-testid="sessions-list">
      <CardHeader>
        <CardTitle>{t("heading")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {sessions.isLoading ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("loading")}</p>
        ) : null}

        {sessions.isError ? (
          <p className="text-sm text-red-800" role="alert">
            {t("loadFailed")}
          </p>
        ) : null}

        {sessions.data && sessions.data.items.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-500)]">{t("empty")}</p>
        ) : null}

        {deleteError ? (
          <p className="text-sm text-red-800" role="alert">
            {deleteError}
          </p>
        ) : null}

        <ul className="space-y-2">
          {sessions.data?.items.map((session) => {
            const createdAt = new Date(session.created_at).toLocaleString();
            const isOpen = openSessionId === session.id;
            const isDeleted = session.deleted_at !== null;
            return (
              <li
                key={session.id}
                className={`rounded-md ring-1 ring-[color:var(--color-border)] p-3 ${isDeleted ? "opacity-60" : ""}`}
                data-testid={`session-item-${session.id}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={statusVariant(session.status)}>{tStatus(session.status)}</Badge>
                  {isDeleted ? <Badge variant="neutral">{t("deletedTombstone")}</Badge> : null}
                  <span className="text-xs text-[color:var(--color-ink-500)]">
                    {t("createdAt", { date: createdAt })}
                  </span>
                  {session.duration_sec !== null && session.duration_sec !== undefined ? (
                    <span className="text-xs text-[color:var(--color-ink-500)]">
                      {t("duration", { seconds: Math.round(session.duration_sec) })}
                    </span>
                  ) : null}
                  {session.language ? (
                    <span className="text-xs text-[color:var(--color-ink-500)]">
                      {t("language", { code: session.language })}
                    </span>
                  ) : null}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    onClick={() => setOpenSessionId(isOpen ? null : session.id)}
                    data-testid={`session-toggle-${session.id}`}
                  >
                    {t("view")}
                  </Button>
                  {!isDeleted ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => onDelete(session.id)}
                      disabled={remove.isPending}
                      data-testid={`session-delete-${session.id}`}
                    >
                      {remove.isPending ? t("deleting") : t("delete")}
                    </Button>
                  ) : null}
                </div>
                {isOpen ? (
                  <div className="mt-3">
                    <TranscriptViewer session={session} />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
