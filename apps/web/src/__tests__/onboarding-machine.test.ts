import { describe, expect, it } from "vitest";

import {
  INITIAL_ONBOARDING_STATE,
  type OnboardingAction,
  type OnboardingState,
  onboardingReducer,
} from "@/lib/onboarding-machine";

/**
 * Reducer-тесты state machine onboarding-wizard'а.
 *
 * Phase 4.12 контракт:
 *   - стартуем в `choose-source`;
 *   - `pick-source` переводит в `import` с выбранным source;
 *   - `back` из `import` возвращает в `choose-source`;
 *   - `submit-import` из `import` переводит в `done`;
 *   - `done` — терминальное (re-dispatch не меняет состояние).
 */
describe("onboardingReducer", () => {
  const reduce = (state: OnboardingState, ...actions: OnboardingAction[]): OnboardingState =>
    actions.reduce((acc, a) => onboardingReducer(acc, a), state);

  it("starts in choose-source step", () => {
    expect(INITIAL_ONBOARDING_STATE.step).toBe("choose-source");
  });

  it("pick-source moves to import with the chosen source", () => {
    const next = reduce(INITIAL_ONBOARDING_STATE, { type: "pick-source", source: "gedcom" });
    expect(next).toEqual({ step: "import", source: "gedcom", treeName: "" });
  });

  it("back from import returns to choose-source (resets source)", () => {
    const next = reduce(
      INITIAL_ONBOARDING_STATE,
      { type: "pick-source", source: "familysearch" },
      { type: "back" },
    );
    expect(next).toEqual(INITIAL_ONBOARDING_STATE);
  });

  it("set-tree-name updates only the treeName field", () => {
    const next = reduce(
      INITIAL_ONBOARDING_STATE,
      { type: "pick-source", source: "blank" },
      { type: "set-tree-name", name: "My family" },
    );
    expect(next).toEqual({ step: "import", source: "blank", treeName: "My family" });
  });

  it("submit-import moves to done preserving source and treeName", () => {
    const next = reduce(
      INITIAL_ONBOARDING_STATE,
      { type: "pick-source", source: "gedcom" },
      { type: "set-tree-name", name: "Smith family" },
      { type: "submit-import" },
    );
    expect(next).toEqual({ step: "done", source: "gedcom", treeName: "Smith family" });
  });

  it("done is terminal — further actions are no-ops", () => {
    const done = reduce(
      INITIAL_ONBOARDING_STATE,
      { type: "pick-source", source: "blank" },
      { type: "set-tree-name", name: "X" },
      { type: "submit-import" },
    );
    const after = reduce(done, { type: "back" }, { type: "pick-source", source: "gedcom" });
    expect(after).toEqual(done);
  });

  it("reset returns to initial state from any step", () => {
    const before = reduce(
      INITIAL_ONBOARDING_STATE,
      { type: "pick-source", source: "gedcom" },
      { type: "submit-import" },
    );
    const after = reduce(before, { type: "reset" });
    expect(after).toEqual(INITIAL_ONBOARDING_STATE);
  });

  it("ignores pick-source when not in choose-source step", () => {
    const inImport = reduce(INITIAL_ONBOARDING_STATE, { type: "pick-source", source: "gedcom" });
    const after = reduce(inImport, { type: "pick-source", source: "blank" });
    expect(after).toEqual(inImport);
  });
});
