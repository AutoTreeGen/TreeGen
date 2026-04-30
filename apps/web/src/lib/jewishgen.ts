/**
 * Билдер deep-link URL'ов на JewishGen Unified Search.
 *
 * Ничего не запрашивает по сети — только формирует URL, который
 * пользователь открывает в новой вкладке. Соответствие CLAUDE.md §5
 * (no scraping platforms without public API): JewishGen публичного API
 * не имеет, поэтому путь — deep-link helper, см. ADR-0058 и
 * docs/research/archive-integrations-2026.md (Phase 9.6a).
 *
 * Каноническая страница unified-search'а — /databases/all/. Каждая
 * search-«линия» в форме описывается тройкой параметров:
 *   srchNv  — data type (S=Surname, G=GivenName, T=Town, X=AnyField)
 *   srchNt  — match type (Q=Phonetically Like — дефолт по UI, лучший
 *             вариант для транслитерированных еврейских имён)
 *   srchN   — собственно строка
 * SrchBOOL=AND связывает линии конъюнкцией.
 */
export const JEWISHGEN_SEARCH_BASE = "https://www.jewishgen.org/databases/all/";

export type JewishGenQuery = {
  surname?: string | null;
  givenName?: string | null;
  town?: string | null;
};

type SearchLine = {
  /** Data type — S/G/T/X. */
  field: "S" | "G" | "T" | "X";
  /** Match type — Q (phonetic) by default. */
  match: "Q" | "D" | "S" | "E" | "F1" | "F2" | "FM";
  value: string;
};

function buildLines(query: JewishGenQuery): SearchLine[] {
  const lines: SearchLine[] = [];
  // Surname идёт первой линией — самый сильный фильтр на JG.
  const surname = query.surname?.trim();
  if (surname) {
    lines.push({ field: "S", match: "Q", value: surname });
  }
  const givenName = query.givenName?.trim();
  if (givenName) {
    lines.push({ field: "G", match: "Q", value: givenName });
  }
  const town = query.town?.trim();
  if (town) {
    lines.push({ field: "T", match: "Q", value: town });
  }
  return lines;
}

/**
 * Возвращает deep-link URL на JewishGen Unified Search. Если нет ни
 * одного непустого поля (surname/givenName/town) — возвращает null:
 * вызывающий код должен спрятать кнопку, а не открывать «пустой»
 * поиск.
 */
export function buildJewishGenSearchUrl(query: JewishGenQuery): string | null {
  const lines = buildLines(query);
  if (lines.length === 0) {
    return null;
  }
  const params = new URLSearchParams();
  lines.forEach((line, idx) => {
    const n = idx + 1;
    params.set(`srch${n}v`, line.field);
    params.set(`srch${n}t`, line.match);
    params.set(`srch${n}`, line.value);
  });
  // Между линиями — AND. JG'шный default тоже AND, но передаём явно
  // чтобы поведение не менялось от их UI-state'а.
  params.set("SrchBOOL", "AND");
  return `${JEWISHGEN_SEARCH_BASE}?${params.toString()}`;
}
