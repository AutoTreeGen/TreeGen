import { describe, expect, it } from "vitest";

import enMessages from "../../messages/en.json";
import ruMessages from "../../messages/ru.json";

/**
 * Phase 4.13b — full i18n rollout regression net.
 *
 * Phase 4.13a положил `locale-rendering.test.tsx` с двумя гарантиями:
 *
 * 1. Каждый ключ из en.json существует в ru.json и наоборот (parity).
 * 2. Для конкретных компонентов (ErrorMessage) en !== ru.
 *
 * 4.13b добавил большой scope (trees/[id]/*, persons/*, dna/*, hypotheses/*,
 * sources/*, familysearch/*) — 29 новых ключей в ~10 sub-namespaces. Этот
 * тест ловит две специфичные регрессии:
 *
 * * **Forgot-to-translate**: ru-значение скопировано из en дословно. parity-
 *   тест это пропустит (ключи совпадают, значения «есть»). Мы fail'имся, если
 *   доля совпадений по новому scope превышает порог.
 * * **Empty value**: ключ есть, но строка пустая — парсер missing-key
 *   fallback'ом не подставит, но UI окажется голым.
 */

const NEW_NAMESPACES_4_13B: ReadonlyArray<string> = [
  "trees.access",
  "trees.duplicates",
  "trees.hypotheses",
  "trees.import",
  "trees.persons",
  "persons.detail",
  "persons.tree",
  "persons.mergeRoute",
  "dna.list",
  "dna.kitMatches",
  "dna.matchDetail",
  "hypotheses.detail",
  "sources.detail",
  "familysearch.connect",
  "familysearch.importStatus",
  "familysearch.preview",
];

function getNamespace(messages: unknown, path: string): Record<string, unknown> | null {
  const parts = path.split(".");
  let cur: unknown = messages;
  for (const p of parts) {
    if (typeof cur !== "object" || cur === null) return null;
    cur = (cur as Record<string, unknown>)[p];
  }
  return typeof cur === "object" && cur !== null ? (cur as Record<string, unknown>) : null;
}

function flatten(obj: unknown, prefix = ""): Record<string, string> {
  if (typeof obj !== "object" || obj === null) return {};
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      result[path] = value;
    } else if (typeof value === "object" && value !== null) {
      Object.assign(result, flatten(value, path));
    }
  }
  return result;
}

describe("Phase 4.13b — new namespaces present in both locales", () => {
  it.each(NEW_NAMESPACES_4_13B)("namespace %s exists in en + ru with at least one key", (ns) => {
    const enNs = getNamespace(enMessages, ns);
    const ruNs = getNamespace(ruMessages, ns);
    expect(enNs, `en.json missing namespace ${ns}`).not.toBeNull();
    expect(ruNs, `ru.json missing namespace ${ns}`).not.toBeNull();
    expect(Object.keys(enNs ?? {}).length, `en.${ns} is empty`).toBeGreaterThan(0);
    expect(Object.keys(ruNs ?? {}).length, `ru.${ns} is empty`).toBeGreaterThan(0);
  });

  it("no value in any new namespace is an empty string", () => {
    const allFlat = { ...flatten(enMessages), ...flatten(ruMessages) };
    const empties = Object.entries(allFlat)
      .filter(([k]) => NEW_NAMESPACES_4_13B.some((ns) => k.startsWith(`${ns}.`)))
      .filter(([, v]) => typeof v === "string" && v.trim() === "");
    expect(empties).toEqual([]);
  });
});

describe("Phase 4.13b — Russian was actually translated, not copy-pasted from English", () => {
  it("at most 5% of new keys have identical en and ru values (allows ICU vars / proper nouns)", () => {
    const enFlat = flatten(enMessages);
    const ruFlat = flatten(ruMessages);
    const newKeys = Object.keys(enFlat).filter((k) =>
      NEW_NAMESPACES_4_13B.some((ns) => k.startsWith(`${ns}.`)),
    );
    const identical = newKeys.filter((k) => enFlat[k] === ruFlat[k]);
    // Допуск на короткие/числовые/имя-собственные строки ("FamilySearch", "Min cM").
    // Если выйдем за 5%, значит кто-то копипастил en в ru.
    const ratio = identical.length / Math.max(newKeys.length, 1);
    expect(ratio, `identical en/ru: ${identical.join(", ")}`).toBeLessThanOrEqual(0.05);
  });
});
