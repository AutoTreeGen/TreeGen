"use client";

/**
 * Phase 10.9d — read-only рендер транскрипта.
 *
 * 10.9a — read-only. Edit-mode (правка распознанного текста) — 10.9d
 * UI-iteration; здесь только рендер + caveat-баннер «авто-транскрипт,
 * проверьте перед использованием».
 */

import { useTranslations } from "next-intl";

import type { AudioSessionResponse } from "@/lib/voice-api";

export type TranscriptViewerProps = {
  session: AudioSessionResponse;
};

export function TranscriptViewer({ session }: TranscriptViewerProps) {
  const t = useTranslations("voice.transcript");
  const tMeta = useTranslations("voice.transcript.metadata");

  if (session.status === "failed") {
    return (
      <div className="space-y-2" data-testid="transcript-viewer">
        <p className="text-sm text-red-800" role="alert">
          {t("errorPrefix", { message: session.error_message ?? "" })}
        </p>
      </div>
    );
  }

  if (!session.transcript_text) {
    return (
      <div className="space-y-2" data-testid="transcript-viewer">
        <p className="text-sm text-[color:var(--color-ink-500)]">{t("empty")}</p>
      </div>
    );
  }

  // Whisper стоимость приходит как Decimal-string из FastAPI; парсим один
  // раз для UX-форматирования. Невалидный input → null, скрываем поле.
  const cost =
    session.transcript_cost_usd !== null && session.transcript_cost_usd !== undefined
      ? Number(session.transcript_cost_usd)
      : null;

  return (
    <div className="space-y-2" data-testid="transcript-viewer">
      <h4 className="text-sm font-medium">{t("heading")}</h4>
      <p className="rounded-md bg-[color:var(--color-surface-muted)] p-3 text-sm leading-relaxed whitespace-pre-wrap">
        {session.transcript_text}
      </p>
      <p
        className="text-xs italic text-[color:var(--color-ink-500)]"
        data-testid="transcript-caveat"
      >
        {t("caveat")}
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-[color:var(--color-ink-500)]">
        {session.transcript_provider ? (
          <span>{tMeta("provider", { provider: session.transcript_provider })}</span>
        ) : null}
        {session.transcript_model_version ? (
          <span>{tMeta("model", { model: session.transcript_model_version })}</span>
        ) : null}
        {session.language ? <span>{tMeta("language", { code: session.language })}</span> : null}
        {session.duration_sec !== null && session.duration_sec !== undefined ? (
          <span>{tMeta("duration", { seconds: Math.round(session.duration_sec) })}</span>
        ) : null}
        {cost !== null && Number.isFinite(cost) ? (
          <span>{tMeta("cost", { cost: cost.toFixed(4) })}</span>
        ) : null}
      </div>
    </div>
  );
}
