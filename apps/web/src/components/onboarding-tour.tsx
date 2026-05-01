"use client";

import { useUser } from "@clerk/nextjs";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { XMark } from "@/components/icons/x-mark";
import { Button } from "@/components/ui/button";

/**
 * Phase 4.15 — interactive onboarding tour.
 *
 * Реализация — собственная (без react-joyride). Обоснование — ADR-0061:
 *
 * - 5–7 шагов модального типа: card в центре viewport'а с back / next /
 *   skip / close. Опционально подсвечивает якорный DOM-элемент по
 *   ``data-tour-id`` (scroll into view + ring-overlay) — без spotlight'а
 *   как в react-joyride, чтобы не тащить лишнюю portal-логику и оставаться
 *   robust на любом маршруте.
 * - Persistence — Clerk ``unsafeMetadata.tour`` (см. ниже). Совпадает с
 *   паттерном locale-dual-write в `/settings` (ADR-0038), один источник
 *   правды о user-preference на frontend'е.
 * - Auto-mount только для signed-in user'ов и ТОЛЬКО на `/dashboard`
 *   (минимизирует «выскакивает в неподходящих местах»). Для повторного
 *   запуска — кнопка в `/settings` (`?restartTour=1` query-параметр).
 */

// ---------------------------------------------------------------------------
// Persistence — Clerk unsafeMetadata.tour
// ---------------------------------------------------------------------------

export type TourState = {
  tour_completed?: boolean;
  tour_skipped?: boolean;
  tour_completed_at?: string | null;
};

type ClerkUserLike = {
  unsafeMetadata: Record<string, unknown>;
  update: (patch: { unsafeMetadata: Record<string, unknown> }) => Promise<unknown>;
};

export function readTourState(user: ClerkUserLike | null | undefined): TourState {
  if (!user) return {};
  const raw = (user.unsafeMetadata?.tour ?? {}) as TourState;
  return {
    tour_completed: Boolean(raw.tour_completed),
    tour_skipped: Boolean(raw.tour_skipped),
    tour_completed_at: raw.tour_completed_at ?? null,
  };
}

export async function writeTourState(user: ClerkUserLike, patch: TourState): Promise<void> {
  const current = (user.unsafeMetadata?.tour ?? {}) as TourState;
  await user.update({
    unsafeMetadata: {
      ...user.unsafeMetadata,
      tour: { ...current, ...patch },
    },
  });
}

// ---------------------------------------------------------------------------
// Step model
// ---------------------------------------------------------------------------

const STEPS: { key: string; anchorTourId?: string }[] = [
  { key: "step1" }, // welcome — no anchor
  { key: "step2", anchorTourId: "import-gedcom" },
  { key: "step3", anchorTourId: "search-persons" },
  { key: "step4", anchorTourId: "tree-visualization" },
  { key: "step5", anchorTourId: "dna-matches" },
  { key: "step6", anchorTourId: "hypotheses" },
  { key: "step7", anchorTourId: "share-tree" },
];

export const TOUR_TOTAL_STEPS = STEPS.length;

// ---------------------------------------------------------------------------
// Public mount component
// ---------------------------------------------------------------------------

/**
 * Mount-точка тура. Безопасна для рендера в root layout — внутри сама
 * решает, показывать ли overlay (auto-trigger на /dashboard для new
 * user'ов, manual trigger через `?restartTour=1`).
 */
export function OnboardingTour() {
  const { user, isLoaded } = useUser();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  // sessionDismissedRef — один раз закрыли overlay в этой сессии (close или
  // skip/finish), больше auto-trigger не сработает. Без этого useEffect ниже
  // снова откроет overlay после ``setOpen(false)`` в close-handler'е, потому
  // что ``open`` присутствует в dep-массиве.
  const sessionDismissedRef = useRef(false);

  // Нормализуем path: query-string в Next 13+ читается отдельно, но для
  // тестов мы не зависим от useSearchParams() — проверяем `?restartTour=1`
  // через window.location.search напрямую.
  const restartRequested =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("restartTour") === "1";

  // Auto-trigger логика. Один раз после Clerk-loaded:
  //   - manual: ?restartTour=1 → всегда открываем (даже если completed)
  //   - auto:   /dashboard + tour_completed=false + tour_skipped=false
  useEffect(() => {
    if (!isLoaded || !user) return;
    if (open || sessionDismissedRef.current) return;
    const state = readTourState(user as ClerkUserLike);

    if (restartRequested) {
      setStepIndex(0);
      setOpen(true);
      return;
    }
    if (pathname !== "/dashboard") return;
    if (state.tour_completed || state.tour_skipped) return;
    setStepIndex(0);
    setOpen(true);
  }, [isLoaded, user, pathname, restartRequested, open]);

  const dismiss = () => {
    sessionDismissedRef.current = true;
    setOpen(false);
  };

  if (!isLoaded || !user || !open) {
    return null;
  }

  return (
    <TourOverlay
      stepIndex={stepIndex}
      onStepChange={setStepIndex}
      onSkip={async () => {
        await writeTourState(user as ClerkUserLike, {
          tour_skipped: true,
          tour_completed: false,
          tour_completed_at: null,
        });
        dismiss();
      }}
      onClose={dismiss}
      onFinish={async () => {
        await writeTourState(user as ClerkUserLike, {
          tour_completed: true,
          tour_skipped: false,
          tour_completed_at: new Date().toISOString(),
        });
        dismiss();
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Overlay (visual-only)
// ---------------------------------------------------------------------------

function TourOverlay({
  stepIndex,
  onStepChange,
  onSkip,
  onClose,
  onFinish,
}: {
  stepIndex: number;
  onStepChange: (next: number) => void;
  onSkip: () => void | Promise<void>;
  onClose: () => void;
  onFinish: () => void | Promise<void>;
}) {
  const t = useTranslations("onboarding.tour");
  const step = STEPS[stepIndex];
  const isLast = stepIndex === STEPS.length - 1;
  const isFirst = stepIndex === 0;

  // Якорь — best-effort highlight. Если элемент с ``data-tour-id``
  // существует на текущей странице, скроллим к нему и подсвечиваем
  // ring'ом. Иначе показываем только центрированный card.
  useEffect(() => {
    if (!step?.anchorTourId) return;
    const el = document.querySelector<HTMLElement>(`[data-tour-id="${step.anchorTourId}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("ring-2", "ring-[color:var(--color-accent)]", "rounded-md");
    return () => {
      el.classList.remove("ring-2", "ring-[color:var(--color-accent)]", "rounded-md");
    };
  }, [step]);

  if (!step) return null;

  const stepTitle = t(`${step.key}.title`);
  const stepBody = t(`${step.key}.body`);

  return (
    <dialog
      open
      className="fixed inset-0 z-50 m-0 flex h-full w-full items-center justify-center bg-black/40 p-4"
      aria-labelledby="onboarding-tour-title"
      data-testid="onboarding-tour"
    >
      <div className="w-full max-w-md rounded-lg bg-[color:var(--color-surface)] p-6 shadow-xl">
        <div className="mb-2 flex items-center justify-between">
          <span
            className="text-xs uppercase tracking-wide text-[color:var(--color-ink-500)]"
            data-testid="onboarding-tour-step-label"
          >
            {t("stepLabel", { current: stepIndex + 1, total: STEPS.length })}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            data-testid="onboarding-tour-close"
            className="text-[color:var(--color-ink-500)] hover:text-[color:var(--color-ink-900)]"
          >
            <XMark className="h-4 w-4" />
          </button>
        </div>
        <h2
          id="onboarding-tour-title"
          className="text-lg font-semibold text-[color:var(--color-ink-900)]"
          data-testid="onboarding-tour-title"
        >
          {stepTitle}
        </h2>
        <p className="mt-2 text-sm text-[color:var(--color-ink-700)]">{stepBody}</p>

        <TourFooter
          isFirst={isFirst}
          isLast={isLast}
          onBack={() => onStepChange(Math.max(0, stepIndex - 1))}
          onNext={() => onStepChange(Math.min(STEPS.length - 1, stepIndex + 1))}
          onSkip={onSkip}
          onFinish={onFinish}
        />
      </div>
    </dialog>
  );
}

function TourFooter({
  isFirst,
  isLast,
  onBack,
  onNext,
  onSkip,
  onFinish,
}: {
  isFirst: boolean;
  isLast: boolean;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void | Promise<void>;
  onFinish: () => void | Promise<void>;
}) {
  const t = useTranslations("onboarding.tour");
  return (
    <div className="mt-5 flex items-center justify-between gap-2">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        data-testid="onboarding-tour-skip"
        onClick={() => void onSkip()}
      >
        {t("skip")}
      </Button>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          data-testid="onboarding-tour-back"
          onClick={onBack}
          disabled={isFirst}
        >
          {t("back")}
        </Button>
        {isLast ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            data-testid="onboarding-tour-finish"
            onClick={() => void onFinish()}
          >
            {t("finish")}
          </Button>
        ) : (
          <Button
            type="button"
            variant="primary"
            size="sm"
            data-testid="onboarding-tour-next"
            onClick={onNext}
          >
            {t("next")}
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Restart-tour button (used in /settings)
// ---------------------------------------------------------------------------

/**
 * Кнопка "Restart tour" для settings page. Сбрасывает persistence-флаги в
 * Clerk и редиректит на ``/dashboard?restartTour=1``, где OnboardingTour
 * подхватит query-flag и откроется. Дизайн-выбор: НЕ перезапускать тур
 * inline в settings, потому что многие шаги имеют DOM-якори, которых
 * на settings-странице нет.
 */
export function RestartTourButton({
  navigate,
  className,
}: {
  navigate?: (href: string) => void;
  className?: string;
}) {
  const t = useTranslations("onboarding.tour");
  const { user } = useUser();
  const [busy, setBusy] = useState(false);

  const onClick = async () => {
    if (!user || busy) return;
    setBusy(true);
    try {
      await writeTourState(user as ClerkUserLike, {
        tour_completed: false,
        tour_skipped: false,
        tour_completed_at: null,
      });
      const href = "/dashboard?restartTour=1";
      if (navigate) {
        navigate(href);
      } else if (typeof window !== "undefined") {
        window.location.href = href;
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      type="button"
      variant="secondary"
      size="md"
      onClick={onClick}
      disabled={busy || !user}
      data-testid="restart-tour"
      className={className}
    >
      {busy ? "…" : t("restart")}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// Tour anchor — helper для проставления data-tour-id (опционально)
// ---------------------------------------------------------------------------

/**
 * Опциональный wrapper для разметки tour-якорей в JSX. Эквивалент
 * прямого добавления ``data-tour-id``, но удобнее когда target — уже
 * существующий element без места под extra prop'ы.
 */
export function TourAnchor({
  id,
  children,
  className,
}: {
  id: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span data-tour-id={id} className={className}>
      {children}
    </span>
  );
}
