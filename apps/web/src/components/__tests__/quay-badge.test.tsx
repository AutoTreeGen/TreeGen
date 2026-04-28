import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QuayBadge } from "@/components/quay-badge";

describe("QuayBadge", () => {
  // Один тест на каждое значение GEDCOM QUAY (см. ADR-0015 §QUAY mapping).
  // Семантика label'а — именно та, которую UI показывает в source-detail
  // и persons-citation секциях.
  it.each([
    [3, "QUAY 3 · primary", /emerald/],
    [2, "QUAY 2 · good", /sky/],
    [1, "QUAY 1 · weak", /amber/],
    [0, "QUAY 0 · unreliable", /red/],
  ] as const)("renders raw=%i with label %s and expected tone", (raw, label, palette) => {
    render(<QuayBadge raw={raw} />);
    const el = screen.getByText(label);
    expect(el).toBeInTheDocument();
    expect(el.className).toMatch(palette);
  });

  it("renders null/undefined as neutral 'unknown'", () => {
    const { rerender } = render(<QuayBadge raw={null} />);
    expect(screen.getByText("QUAY · unknown")).toBeInTheDocument();
    rerender(<QuayBadge raw={undefined} />);
    expect(screen.getByText("QUAY · unknown")).toBeInTheDocument();
  });
});
