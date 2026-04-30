import { describe, expect, it } from "vitest";

import enMessages from "../../messages/en.json";
import ruMessages from "../../messages/ru.json";

/**
 * Phase 11.1 — locale parity для namespace `sharing.*`.
 *
 * После того как мы добавили sharing-специфичные строки, тест
 * `locale-rendering.test.tsx` уже проверяет общий subset (en ⊆ ru, ru ⊆ en).
 * Здесь дополнительно явно проверяем, что **все** ключи sharing.* существуют
 * в обеих локалях И тексты отличаются (нет случайной англо-копии в ru).
 */

function flatten(obj: unknown, prefix = ""): Record<string, string> {
  if (typeof obj !== "object" || obj === null) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      out[path] = value;
    } else if (typeof value === "object" && value !== null) {
      Object.assign(out, flatten(value, path));
    }
  }
  return out;
}

describe("sharing.* locale parity", () => {
  const enFlat = flatten(enMessages);
  const ruFlat = flatten(ruMessages);

  const sharingEnKeys = Object.keys(enFlat).filter((k) => k.startsWith("sharing."));
  const sharingRuKeys = Object.keys(ruFlat).filter((k) => k.startsWith("sharing."));

  it("declares ≥10 sharing keys (sanity)", () => {
    expect(sharingEnKeys.length).toBeGreaterThanOrEqual(10);
  });

  it("every sharing.* key in en.json exists in ru.json", () => {
    const missing = sharingEnKeys.filter((k) => !(k in ruFlat));
    expect(missing).toEqual([]);
  });

  it("every sharing.* key in ru.json exists in en.json (no orphans)", () => {
    const missing = sharingRuKeys.filter((k) => !(k in enFlat));
    expect(missing).toEqual([]);
  });

  it("ru translations differ from en for non-trivial keys (no English bleed-through)", () => {
    // Некоторые «технические» строки совпадают между локалями (email
    // placeholder'ы, бренд-имена, идентификаторы). Скипаем их, чтобы тест
    // ловил только реальный английский bleed-through.
    const suspicious: string[] = [];
    for (const key of sharingEnKeys) {
      const en = enFlat[key];
      const ru = ruFlat[key];
      if (!en || !ru) continue;
      if (en.length < 12) continue;
      if (key.toLowerCase().endsWith("placeholder")) continue;
      if (en === ru) {
        suspicious.push(key);
      }
    }
    expect(suspicious).toEqual([]);
  });

  it("renders sharing.owner.title differently in en vs ru", () => {
    expect(enFlat["sharing.owner.title"]).toBe("Sharing");
    expect(ruFlat["sharing.owner.title"]).toBe("Совместный доступ");
  });
});
