"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { type JewishGenQuery, buildJewishGenSearchUrl } from "@/lib/jewishgen";

/**
 * Кнопка-deep-link на JewishGen Unified Search для конкретной персоны.
 *
 * Никаких сетевых запросов на JewishGen со стороны TreeGen — кнопка
 * просто открывает заранее сконструированный URL в новой вкладке.
 * Соответствие CLAUDE.md §5 («no scraping platforms without public
 * API»), см. ADR-0058.
 *
 * Если у нас нет ни одного непустого поля для запроса
 * (`buildJewishGenSearchUrl` вернул `null`) — компонент возвращает
 * `null` и ничего не рендерит.
 */
export function JewishGenSearchButton({ query }: { query: JewishGenQuery }) {
  const t = useTranslations("persons.detail.externalSearch");
  const url = buildJewishGenSearchUrl(query);
  if (!url) {
    return null;
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Button variant="secondary" size="sm" asChild>
        <a href={url} target="_blank" rel="noopener noreferrer" data-testid="jewishgen-search-link">
          {t("jewishgen.cta")}
          <span aria-hidden="true">↗</span>
        </a>
      </Button>
      <p className="text-xs text-[color:var(--color-ink-500)]">{t("jewishgen.disclaimer")}</p>
    </div>
  );
}
