/**
 * Smoke-тест Design System v1 (DS-1 / ADR-0067).
 *
 * Проверяет, что:
 * - apps/web/src/styles/design-system.css физически присутствует;
 * - PT Serif прописан в @font-face (не Manrope, DS-1 fix #1);
 * - apps/web/src/app/globals.css импортирует design-system.css;
 * - токены light-only — нет упоминаний `[data-theme="dark"]` /
 *   `prefers-color-scheme: dark` / "dark mode" в скопированной копии.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __dirname_ = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(__dirname_, "..", "..");
const TOKENS_CSS = resolve(APP_ROOT, "src", "styles", "design-system.css");
const GLOBALS_CSS = resolve(APP_ROOT, "src", "app", "globals.css");

describe("design-system v1 — apps/web wiring (ADR-0067)", () => {
  it("design-system.css присутствует и читается", () => {
    const css = readFileSync(TOKENS_CSS, "utf-8");
    expect(css.length).toBeGreaterThan(1000);
  });

  it("PT Serif прописан как display-шрифт (не Manrope)", () => {
    const css = readFileSync(TOKENS_CSS, "utf-8");
    expect(css).toMatch(/font-family:\s*"PT Serif"/);
    expect(css).not.toMatch(/Manrope/i);
  });

  it("ссылается на vendored ttf через /fonts/ (Next.js public/)", () => {
    const css = readFileSync(TOKENS_CSS, "utf-8");
    expect(css).toMatch(/url\("\/fonts\/PTSerif-Regular\.ttf"\)/);
  });

  it("--font-display резолвится на PT Serif с serif-fallback", () => {
    const css = readFileSync(TOKENS_CSS, "utf-8");
    expect(css).toMatch(/--font-display:\s*"PT Serif"/);
  });

  it("apps/web globals.css импортирует design-system.css", () => {
    const css = readFileSync(GLOBALS_CSS, "utf-8");
    expect(css).toMatch(/@import\s+["']\.\.\/styles\/design-system\.css["']/);
  });

  it.each([/dark[ -]mode/i, /prefers-color-scheme/i, /\[data-theme="?dark"?\]/])(
    "design-system.css не содержит light-only-violation паттерн %s",
    (pattern) => {
      const css = readFileSync(TOKENS_CSS, "utf-8");
      expect(css).not.toMatch(pattern);
    },
  );
});
