import { describe, expect, it } from "vitest";

import { JEWISHGEN_SEARCH_BASE, buildJewishGenSearchUrl } from "@/lib/jewishgen";

function expectUrl(value: string | null): string {
  // Type-narrowing helper: тестирует «not null» через expect и
  // одновременно сужает тип, чтобы дальше URL-парсинг был type-safe
  // без non-null assertion'а (biome lint/style/noNonNullAssertion).
  expect(value).not.toBeNull();
  if (value === null) {
    throw new Error("buildJewishGenSearchUrl returned null unexpectedly");
  }
  return value;
}

describe("buildJewishGenSearchUrl", () => {
  it("returns null when query is fully empty", () => {
    expect(buildJewishGenSearchUrl({})).toBeNull();
    expect(buildJewishGenSearchUrl({ surname: null, givenName: null, town: null })).toBeNull();
    expect(buildJewishGenSearchUrl({ surname: "", givenName: "   ", town: "" })).toBeNull();
  });

  it("builds surname-only URL with phonetic match and AND boolean", () => {
    const url = expectUrl(buildJewishGenSearchUrl({ surname: "Cohen" }));
    const u = new URL(url);
    expect(`${u.origin}${u.pathname}`).toBe(JEWISHGEN_SEARCH_BASE);
    expect(u.searchParams.get("srch1v")).toBe("S");
    expect(u.searchParams.get("srch1t")).toBe("Q");
    expect(u.searchParams.get("srch1")).toBe("Cohen");
    expect(u.searchParams.get("SrchBOOL")).toBe("AND");
    // Только одна линия — никаких srch2*.
    expect(u.searchParams.has("srch2")).toBe(false);
  });

  it("composes multiple lines in order surname → given → town", () => {
    const url = expectUrl(
      buildJewishGenSearchUrl({
        surname: "Goldberg",
        givenName: "Moshe",
        town: "Vilna",
      }),
    );
    const u = new URL(url);
    expect(u.searchParams.get("srch1v")).toBe("S");
    expect(u.searchParams.get("srch1")).toBe("Goldberg");
    expect(u.searchParams.get("srch2v")).toBe("G");
    expect(u.searchParams.get("srch2")).toBe("Moshe");
    expect(u.searchParams.get("srch3v")).toBe("T");
    expect(u.searchParams.get("srch3")).toBe("Vilna");
  });

  it("skips empty fields and renumbers remaining lines", () => {
    // Нет surname — тогда givenName становится первой линией.
    const url = expectUrl(
      buildJewishGenSearchUrl({
        surname: "",
        givenName: "Rivka",
        town: "Lodz",
      }),
    );
    const u = new URL(url);
    expect(u.searchParams.get("srch1v")).toBe("G");
    expect(u.searchParams.get("srch1")).toBe("Rivka");
    expect(u.searchParams.get("srch2v")).toBe("T");
    expect(u.searchParams.get("srch2")).toBe("Lodz");
    expect(u.searchParams.has("srch3")).toBe(false);
  });

  it("trims whitespace before encoding", () => {
    const url = expectUrl(buildJewishGenSearchUrl({ surname: "  Levy  " }));
    const u = new URL(url);
    expect(u.searchParams.get("srch1")).toBe("Levy");
  });

  it("URL-encodes special characters and Cyrillic surnames", () => {
    // Eastern European Jewish surname в кириллице — JG'шный поиск
    // вряд ли что-то найдёт по такому варианту, но URL должен быть
    // корректно сформирован, а не сломан. Перекодировка в латиницу —
    // separate concern (см. ADR-0058 «отложено»).
    const url = expectUrl(buildJewishGenSearchUrl({ surname: "Гольдберг" }));
    expect(url).toContain("srch1=");
    const u = new URL(url);
    expect(u.searchParams.get("srch1")).toBe("Гольдберг");
  });

  it("encodes ampersands and reserved characters in values without breaking the URL", () => {
    const url = expectUrl(buildJewishGenSearchUrl({ surname: "O'Brien & Sons" }));
    const u = new URL(url);
    expect(u.searchParams.get("srch1")).toBe("O'Brien & Sons");
  });

  it("returns a URL pointing to the canonical /databases/all/ endpoint", () => {
    const url = buildJewishGenSearchUrl({ surname: "Cohen" });
    expect(url).toMatch(/^https:\/\/www\.jewishgen\.org\/databases\/all\/\?/);
  });
});
