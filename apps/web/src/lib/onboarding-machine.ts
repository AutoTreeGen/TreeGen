/**
 * Phase 4.12 — onboarding wizard state machine.
 *
 * Pure-function reducer (`onboardingReducer`) — тестируем без рендера.
 * Валидные переходы:
 *
 *   choose-source ──pick(source)──▶ import
 *                  ◀──back──────────
 *
 *   import ──submit──▶ done
 *           ──back───▶ choose-source
 *
 *   done — терминальное (CTA ведут наружу).
 *
 * Используется в apps/web/src/app/onboarding/page.tsx.
 */

export type OnboardingSource = "gedcom" | "familysearch" | "blank";

export type OnboardingState =
  | { step: "choose-source"; source: null }
  | { step: "import"; source: OnboardingSource; treeName: string }
  | { step: "done"; source: OnboardingSource; treeName: string };

export type OnboardingAction =
  | { type: "pick-source"; source: OnboardingSource }
  | { type: "back" }
  | { type: "set-tree-name"; name: string }
  | { type: "submit-import" }
  | { type: "reset" };

export const INITIAL_ONBOARDING_STATE: OnboardingState = { step: "choose-source", source: null };

export function onboardingReducer(
  state: OnboardingState,
  action: OnboardingAction,
): OnboardingState {
  switch (action.type) {
    case "pick-source": {
      if (state.step !== "choose-source") return state;
      return { step: "import", source: action.source, treeName: "" };
    }
    case "back": {
      if (state.step === "import") return INITIAL_ONBOARDING_STATE;
      return state;
    }
    case "set-tree-name": {
      if (state.step !== "import") return state;
      return { ...state, treeName: action.name };
    }
    case "submit-import": {
      if (state.step !== "import") return state;
      return { step: "done", source: state.source, treeName: state.treeName };
    }
    case "reset":
      return INITIAL_ONBOARDING_STATE;
  }
}
