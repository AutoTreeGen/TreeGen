import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChromosomePainting } from "@/components/chromosome-painting";

describe("ChromosomePainting", () => {
  // Phase 6.3 / ADR-0033: chromosome painting рендерит ровно 22 autosomes
  // + X (23 строки), независимо от количества входных сегментов.
  it("renders all 23 chromosome rows even when no segments provided", () => {
    const { container } = render(<ChromosomePainting segments={[]} />);
    const rows = container.querySelectorAll("g[data-chromosome]");
    expect(rows).toHaveLength(23);
    const keys = Array.from(rows).map((g) => g.getAttribute("data-chromosome"));
    expect(keys).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
      "10",
      "11",
      "12",
      "13",
      "14",
      "15",
      "16",
      "17",
      "18",
      "19",
      "20",
      "21",
      "22",
      "X",
    ]);
  });

  it("renders one rect per shared segment in matching chromosome", () => {
    const { getByTestId } = render(
      <ChromosomePainting
        segments={[
          { chromosome: 1, start_bp: 1_000_000, end_bp: 50_000_000, cm: 30, num_snps: 1234 },
          { chromosome: 7, start_bp: 80_000_000, end_bp: 120_000_000, cm: 40, num_snps: 5678 },
          { chromosome: 7, start_bp: 130_000_000, end_bp: 159_138_000, cm: 18, num_snps: 999 },
        ]}
      />,
    );
    expect(getByTestId("segment-chr1-0")).toBeInTheDocument();
    expect(getByTestId("segment-chr7-0")).toBeInTheDocument();
    expect(getByTestId("segment-chr7-1")).toBeInTheDocument();
  });

  it("ignores invalid chromosome values (Y, MT, garbage)", () => {
    const { container } = render(
      <ChromosomePainting
        segments={[
          // Y и MT не входят в Phase 6.1 algorithm — должны быть просто отфильтрованы.
          { chromosome: 23 as unknown as number, start_bp: 0, end_bp: 1000, cm: 1, num_snps: 1 },
          { chromosome: 0, start_bp: 0, end_bp: 1000, cm: 1, num_snps: 1 },
        ]}
      />,
    );
    const segments = container.querySelectorAll("[data-testid^='segment-']");
    expect(segments).toHaveLength(0);
  });

  it("renders X chromosome segments under the X row", () => {
    const { getByTestId } = render(
      <ChromosomePainting
        segments={[
          {
            chromosome: "X" as unknown as number,
            start_bp: 1_000_000,
            end_bp: 100_000_000,
            cm: 50,
            num_snps: 8000,
          },
        ]}
      />,
    );
    expect(getByTestId("segment-chrX-0")).toBeInTheDocument();
  });

  it("uses provided ariaLabel for accessibility", () => {
    const { container } = render(
      <ChromosomePainting segments={[]} ariaLabel="Shared DNA with John Doe" />,
    );
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-label", "Shared DNA with John Doe");
  });

  // Snapshot — фиксирует геометрию (длины треков, координаты сегментов),
  // чтобы дальнейшие правки SVG не «уплыли» незаметно.
  it("matches the rendered SVG snapshot for a known set of segments", () => {
    const { container } = render(
      <ChromosomePainting
        segments={[
          { chromosome: 1, start_bp: 10_000_000, end_bp: 100_000_000, cm: 50, num_snps: 5000 },
          { chromosome: 21, start_bp: 5_000_000, end_bp: 35_000_000, cm: 25, num_snps: 2000 },
        ]}
        ariaLabel="snapshot test"
      />,
    );
    expect(container.firstChild).toMatchSnapshot();
  });
});
