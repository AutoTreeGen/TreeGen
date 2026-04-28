import { describe, expect, it } from "vitest";

import { DEFAULT_LOCALE, asSupportedLocale, detectLocaleFromAcceptLanguage } from "@/i18n/config";

describe("asSupportedLocale", () => {
  it("returns default for null/empty", () => {
    expect(asSupportedLocale(null)).toBe(DEFAULT_LOCALE);
    expect(asSupportedLocale(undefined)).toBe(DEFAULT_LOCALE);
    expect(asSupportedLocale("")).toBe(DEFAULT_LOCALE);
  });

  it("returns the locale itself when supported", () => {
    expect(asSupportedLocale("en")).toBe("en");
    expect(asSupportedLocale("ru")).toBe("ru");
  });

  it("strips region tag (en-US → en)", () => {
    expect(asSupportedLocale("en-US")).toBe("en");
    expect(asSupportedLocale("ru-RU")).toBe("ru");
  });

  it("returns default for unsupported locales", () => {
    expect(asSupportedLocale("de")).toBe(DEFAULT_LOCALE);
    expect(asSupportedLocale("uk")).toBe(DEFAULT_LOCALE);
  });
});

describe("detectLocaleFromAcceptLanguage", () => {
  it("returns default for null", () => {
    expect(detectLocaleFromAcceptLanguage(null)).toBe(DEFAULT_LOCALE);
  });

  it("picks first supported locale from header", () => {
    expect(detectLocaleFromAcceptLanguage("ru-RU,ru;q=0.9,en;q=0.8")).toBe("ru");
    expect(detectLocaleFromAcceptLanguage("en-US,en;q=0.9")).toBe("en");
  });

  it("falls through to second tag if first is unsupported", () => {
    expect(detectLocaleFromAcceptLanguage("de-DE,ru;q=0.9")).toBe("ru");
  });

  it("returns default when no supported tag is present", () => {
    expect(detectLocaleFromAcceptLanguage("de,fr,zh")).toBe(DEFAULT_LOCALE);
  });
});
