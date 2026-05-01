import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { JewishGenSearchButton } from "@/components/jewishgen-search-button";
import enMessages from "../../messages/en.json";
import ruMessages from "../../messages/ru.json";

function withIntl(children: ReactNode, locale: "en" | "ru" = "en") {
  const messages = locale === "en" ? enMessages : ruMessages;
  return (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
}

describe("<JewishGenSearchButton>", () => {
  it("renders a link that opens JewishGen Unified Search in a new tab", () => {
    render(withIntl(<JewishGenSearchButton query={{ surname: "Cohen" }} />));
    const link = screen.getByTestId("jewishgen-search-link") as HTMLAnchorElement;
    expect(link.href).toMatch(/^https:\/\/www\.jewishgen\.org\/databases\/all\/\?/);
    expect(link.target).toBe("_blank");
    // noopener + noreferrer обязательны для target=_blank на untrusted
    // outbound link'ах: защита от tabnabbing'а и от утечки Referer.
    expect(link.rel.split(/\s+/)).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
  });

  it("renders nothing when there are no usable query fields", () => {
    const { container } = render(withIntl(<JewishGenSearchButton query={{}} />));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("jewishgen-search-link")).toBeNull();
  });

  it("renders the disclaimer/attribution text", () => {
    render(withIntl(<JewishGenSearchButton query={{ surname: "Levy" }} />));
    // Не attaching'аемся к конкретной формулировке — проверяем что
    // disclaimer от next-intl не отдаётся как [missing: ...] fallback.
    const cta = screen.getByTestId("jewishgen-search-link");
    expect(cta.textContent).not.toMatch(/\[missing:/);
    const disclaimer = cta.parentElement?.querySelector("p");
    expect(disclaimer?.textContent ?? "").not.toMatch(/\[missing:/);
    expect(disclaimer?.textContent ?? "").toMatch(/.+/);
  });

  it("renders successfully in Russian locale", () => {
    render(withIntl(<JewishGenSearchButton query={{ surname: "Levy" }} />, "ru"));
    const link = screen.getByTestId("jewishgen-search-link");
    expect(link).toBeInTheDocument();
    expect(link.textContent).not.toMatch(/\[missing:/);
  });
});
