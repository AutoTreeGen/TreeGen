import { describe, expect, it } from "vitest";

import { toRawNode } from "@/components/pedigree-tree";
import type { AncestorTreeNode } from "@/lib/api";

// Хелпер для построения минимального узла с дефолтами — тесты тестируют
// именно конверсию структуры, остальные поля не должны загромождать.
function makeNode(overrides: Partial<AncestorTreeNode> = {}): AncestorTreeNode {
  return {
    id: "p1",
    primary_name: "Test Person",
    birth_year: null,
    death_year: null,
    sex: "U",
    dna_tested: false,
    father: null,
    mother: null,
    ...overrides,
  };
}

describe("toRawNode", () => {
  it("flat-converts AncestorTreeNode → RawNodeDatum with father/mother as children", () => {
    const father = makeNode({ id: "f1", primary_name: "John Sr.", sex: "M" });
    const mother = makeNode({ id: "m1", primary_name: "Jane", sex: "F" });
    const root = makeNode({ id: "p1", primary_name: "Child", sex: "M", father, mother });

    const result = toRawNode(root);

    expect(result.name).toBe("Child");
    expect(result.attributes?.personId).toBe("p1");
    expect(result.children).toHaveLength(2);
    expect(result.children?.[0]?.name).toBe("John Sr.");
    expect(result.children?.[0]?.attributes?.personId).toBe("f1");
    expect(result.children?.[1]?.name).toBe("Jane");
    expect(result.children?.[1]?.attributes?.personId).toBe("m1");
  });

  it("returns children=undefined when both parents are null", () => {
    const orphan = makeNode({ id: "o1", primary_name: "Orphan", father: null, mother: null });

    const result = toRawNode(orphan);

    expect(result.children).toBeUndefined();
  });

  it("passes dna_tested through attributes", () => {
    const tested = makeNode({ id: "t1", dna_tested: true });
    const untested = makeNode({ id: "u1", dna_tested: false });
    const missing = makeNode({ id: "x1", dna_tested: undefined });

    expect(toRawNode(tested).attributes?.dnaTested).toBe(true);
    expect(toRawNode(untested).attributes?.dnaTested).toBe(false);
    // Отсутствующий dna_tested должен схлопываться в false (default).
    expect(toRawNode(missing).attributes?.dnaTested).toBe(false);
  });
});
