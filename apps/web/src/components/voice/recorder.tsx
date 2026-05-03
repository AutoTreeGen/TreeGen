"use client";

/**
 * Phase 10.9d — браузерный recorder voice-to-tree.
 *
 * MediaRecorder + getUserMedia → Blob → POST /trees/{id}/audio-sessions.
 *
 * Дисциплина:
 *   - Recording disabled, пока consent не дан (двойная защита: UI-disable
 *     + backend 403 ``consent_required``).
 *   - Min duration 5 сек / max 5 мин (UX-cap; см. ``WHISPER_MAX_DURATION_SEC``
 *     для абсолютного предела на backend'е).
 *   - Auto-stop по таймеру при достижении max duration.
 *   - WebM/Opus как preferred MIME — Whisper, Chrome, Firefox понимают.
 *     Safari не поддерживает; pre-flight check на ``MediaRecorder.isTypeSupported``
 *     с fallback на ``audio/webm`` вообще без codec (некоторые builds Firefox).
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type AudioSessionResponse, uploadAudioSession } from "@/lib/voice-api";

const MIN_DURATION_SEC = 5;
const MAX_DURATION_SEC = 5 * 60;

/**
 * Phase 10.9e — language picker. Empty value = auto-detect (Whisper guesses).
 * EN — Geoffrey-demo default, regression-stable. RU + HE — multilingual showcase.
 *
 * Не trogает 10.9b extraction layer (он не в main); slice A только пробрасывает
 * Whisper language_hint и помечает upload row.language. Cм. ADR-0080.
 */
const LANGUAGE_OPTIONS = ["", "en", "ru", "he"] as const;
type LanguageOption = (typeof LANGUAGE_OPTIONS)[number];
const NON_LATIN_LANGUAGES: ReadonlySet<LanguageOption> = new Set<LanguageOption>(["ru", "he"]);

/**
 * Подобрать первый supported MIME-type из preferred-списка. ``MediaRecorder``
 * на разных браузерах поддерживает разный набор; webm/opus — самый широкий
 * common denominator.
 */
function pickSupportedMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return null;
}

export type RecorderProps = {
  treeId: string;
  consentGranted: boolean;
  /**
   * Hook для тестов и для page-уровневых эффектов: вызывается после
   * успешной upload'и. Page реагирует — invalidates sessions list,
   * scrolls к новой записи и т.д.
   */
  onUploaded?: (session: AudioSessionResponse) => void;
};

export function Recorder({ treeId, consentGranted, onUploaded }: RecorderProps) {
  const t = useTranslations("voice.recorder");
  const queryClient = useQueryClient();

  const [phase, setPhase] = useState<"idle" | "recording" | "uploading" | "uploaded" | "error">(
    "idle",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  // Phase 10.9e — пользователь выбирает язык до записи. Default "" (Auto).
  // EN-default'а намеренно нет: Whisper сам определит, и мы не хотим чтобы
  // EN-выбор силой подавлял другие языки в Auto-mode'е (ADR-0080 §"Default").
  const [language, setLanguage] = useState<LanguageOption>("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const tickIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef<number>(0);

  const supportedMime = useRef<string | null>(
    typeof window === "undefined" ? null : pickSupportedMimeType(),
  );

  const stopMedia = useCallback(() => {
    if (tickIntervalRef.current !== null) {
      clearInterval(tickIntervalRef.current);
      tickIntervalRef.current = null;
    }
    if (autoStopTimeoutRef.current !== null) {
      clearTimeout(autoStopTimeoutRef.current);
      autoStopTimeoutRef.current = null;
    }
    if (streamRef.current !== null) {
      for (const track of streamRef.current.getTracks()) track.stop();
      streamRef.current = null;
    }
  }, []);

  // На unmount — останавливаем поток (чтобы не оставить «горящий» индикатор
  // микрофона в browser tab'е, если пользователь ушёл с страницы во время записи).
  useEffect(() => {
    return () => {
      stopMedia();
    };
  }, [stopMedia]);

  const upload = useMutation({
    mutationFn: ({
      blob,
      mimeType,
      languageHint,
    }: {
      blob: Blob;
      mimeType: string;
      languageHint: string;
    }) => {
      const ext = mimeType.includes("ogg") ? "ogg" : "webm";
      return uploadAudioSession(treeId, blob, {
        filename: `recording-${Date.now()}.${ext}`,
        languageHint: languageHint.length > 0 ? languageHint : undefined,
      });
    },
    onSuccess: (data) => {
      setPhase("uploaded");
      setErrorMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["audio-sessions", treeId] });
      onUploaded?.(data);
    },
    onError: (err) => {
      setPhase("error");
      const message = err instanceof Error ? err.message : String(err);
      setErrorMessage(t("uploadFailed", { message }));
    },
  });

  const startRecording = async () => {
    setErrorMessage(null);
    if (!supportedMime.current) {
      setPhase("error");
      setErrorMessage(t("unsupported"));
      return;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setPhase("error");
      setErrorMessage(t("unsupported"));
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setPhase("error");
      setErrorMessage(t("deviceUnavailable"));
      return;
    }
    streamRef.current = stream;
    chunksRef.current = [];
    const mimeType = supportedMime.current;
    const recorder = new MediaRecorder(stream, { mimeType });
    recorderRef.current = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunksRef.current.push(event.data);
      }
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType });
      const elapsedSec = (Date.now() - startedAtRef.current) / 1000;
      stopMedia();
      if (elapsedSec < MIN_DURATION_SEC) {
        setPhase("error");
        setErrorMessage(t("minDurationHint", { seconds: MIN_DURATION_SEC }));
        return;
      }
      setPhase("uploading");
      upload.mutate({ blob, mimeType, languageHint: language });
    };
    startedAtRef.current = Date.now();
    setElapsed(0);
    recorder.start();
    setPhase("recording");
    tickIntervalRef.current = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAtRef.current) / 1000);
      setElapsed(seconds);
    }, 250);
    autoStopTimeoutRef.current = setTimeout(() => {
      // Auto-stop при достижении max duration. Recorder.stop() инициирует
      // последний ondataavailable + onstop как в ручном случае.
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }
    }, MAX_DURATION_SEC * 1000);
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  };

  const isRecording = phase === "recording";
  const isUploading = phase === "uploading";
  const recordDisabled = !consentGranted || isUploading;

  return (
    <Card data-testid="voice-recorder">
      <CardHeader>
        <CardTitle>{t("heading")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label
            className="flex items-center gap-2 text-sm text-[color:var(--color-ink-700)]"
            htmlFor="recorder-language"
          >
            <span>{t("languageLabel")}</span>
            <select
              id="recorder-language"
              data-testid="recorder-language"
              className="rounded border border-[color:var(--color-ink-300)] bg-white px-2 py-1 text-sm"
              value={language}
              onChange={(event) => setLanguage(event.target.value as LanguageOption)}
              disabled={isRecording || isUploading}
            >
              <option value="">{t("languageAuto")}</option>
              <option value="en">{t("languageEn")}</option>
              <option value="ru">{t("languageRu")}</option>
              <option value="he">{t("languageHe")}</option>
            </select>
          </label>
        </div>

        {NON_LATIN_LANGUAGES.has(language) ? (
          <p
            className="text-xs text-[color:var(--color-ink-500)]"
            data-testid="recorder-language-hint"
          >
            {t("languageHintNonLatin")}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          {!isRecording ? (
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={() => {
                void startRecording();
              }}
              disabled={recordDisabled}
              data-testid="recorder-start"
            >
              {isUploading ? t("uploading") : t("start")}
            </Button>
          ) : (
            <Button
              type="button"
              variant="destructive"
              size="md"
              onClick={stopRecording}
              data-testid="recorder-stop"
            >
              {t("stop")}
            </Button>
          )}

          {isRecording ? (
            <span
              className="font-mono text-sm text-[color:var(--color-ink-700)]"
              aria-live="polite"
              data-testid="recorder-elapsed"
            >
              {t("elapsed", { seconds: elapsed })}
            </span>
          ) : null}
        </div>

        {!consentGranted ? (
          <p className="text-xs text-[color:var(--color-ink-500)]">{t("consentRequired")}</p>
        ) : (
          <p className="text-xs text-[color:var(--color-ink-500)]">
            {t("minDurationHint", { seconds: MIN_DURATION_SEC })}{" "}
            {t("maxDurationHint", { seconds: MAX_DURATION_SEC })}
          </p>
        )}

        {phase === "uploaded" ? (
          <output className="block text-sm text-green-800">{t("uploaded")}</output>
        ) : null}

        {errorMessage ? (
          <p className="text-sm text-red-800" role="alert">
            {errorMessage}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
