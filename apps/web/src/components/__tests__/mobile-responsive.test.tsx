import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

/**
 * Phase 4.14a — design-system mobile-responsive guarantees.
 *
 * Эти тесты фиксируют классы, которые гарантируют WCAG 2.1 AA touch
 * targets (≥44×44px) и iOS Safari no-zoom поведение (font-size ≥16px
 * на form-controls). Без снапшота — мы хотим explicit failure если
 * кто-то откатит mobile-first классы в Tailwind строке.
 *
 * Принцип: класс `min-h-11` = 44px на mobile, `sm:min-h-0 sm:h-N` =
 * вернуть desktop-высоту на ≥640px. Аналогично для шрифта:
 * `text-base` = 16px на mobile (no iOS zoom), `sm:text-sm` = 14px на
 * desktop (компактный UI).
 */
describe("design-system mobile responsiveness", () => {
  describe("Button", () => {
    it.each([
      ["sm", "min-h-11", "sm:h-8"],
      ["md", "min-h-11", "sm:h-10"],
      ["lg", "min-h-12", "sm:h-12"],
    ] as const)("size=%s имеет mobile floor %s и desktop %s", (size, mobile, desktop) => {
      render(
        <Button size={size} data-testid="btn">
          x
        </Button>,
      );
      const btn = screen.getByTestId("btn");
      expect(btn.className).toContain(mobile);
      expect(btn.className).toContain(desktop);
    });

    it("link variant не получает min-h-11 (inline-текст внутри предложений)", () => {
      render(
        <Button variant="link" size="md" data-testid="btn-link">
          read more
        </Button>,
      );
      const btn = screen.getByTestId("btn-link");
      // compoundVariants добавляет `min-h-0 sm:min-h-0` поверх размера —
      // проверяем что финальный rendered className содержит min-h-0.
      expect(btn.className).toContain("min-h-0");
    });
  });

  describe("Input", () => {
    it("содержит min-h-11 (mobile touch) и sm:h-10 (desktop)", () => {
      render(<Input data-testid="input" placeholder="x" />);
      const input = screen.getByTestId("input");
      expect(input.className).toContain("min-h-11");
      expect(input.className).toContain("sm:h-10");
    });

    it("text-base на mobile (16px → no iOS auto-zoom) и sm:text-sm на desktop", () => {
      render(<Input data-testid="input" placeholder="x" />);
      const input = screen.getByTestId("input");
      expect(input.className).toContain("text-base");
      expect(input.className).toContain("sm:text-sm");
    });
  });

  describe("Checkbox", () => {
    it("h-5 w-5 на mobile (20px hit area) и h-4 w-4 на desktop", () => {
      render(<Checkbox data-testid="cb" />);
      const cb = screen.getByTestId("cb");
      expect(cb.className).toContain("h-5");
      expect(cb.className).toContain("w-5");
      expect(cb.className).toContain("sm:h-4");
      expect(cb.className).toContain("sm:w-4");
    });
  });
});
