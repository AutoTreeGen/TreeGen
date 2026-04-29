import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ConflictResolver, type ResolverSide } from "@/components/person-merge/conflict-resolver";
import enMessages from "../../../../messages/en.json";
import ruMessages from "../../../../messages/ru.json";

/**
 * Phase 6.4 — conflict-resolver tests.
 *
 * Покрывают:
 *   - render для разных типов значений (string / number / null / object);
 *   - radio-выбор инициирует ``onChange`` с правильной стороной;
 *   - identical-плашка появляется когда значения совпадают;
 *   - note-textarea опциональная (рендерится только если ``onNoteChange`` задан).
 */

function withProviders(children: ReactNode, locale: "en" | "ru" = "en") {
  const messages = locale === "en" ? enMessages : ruMessages;
  return (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
}

const fieldTypes: Array<{
  name: string;
  left: unknown;
  right: unknown;
  expectIdentical?: boolean;
}> = [
  { name: "string-different", left: "John", right: "Jonathan" },
  { name: "number-different", left: 0.92, right: 0.71 },
  { name: "null-vs-value", left: null, right: "1850-01-01" },
  { name: "boolean-different", left: true, right: false },
  { name: "object-different", left: { city: "Lviv" }, right: { city: "Lwów" } },
  { name: "identical-strings", left: "Smith", right: "Smith", expectIdentical: true },
];

describe("ConflictResolver", () => {
  it.each(fieldTypes)("renders for $name", ({ name, left, right, expectIdentical }) => {
    const { container } = render(
      withProviders(
        <ConflictResolver
          fieldName={name}
          fieldLabel={`Label for ${name}`}
          leftValue={left}
          rightValue={right}
          selected={null}
          onChange={() => {}}
        />,
      ),
    );
    // Group существует и помечен data-field=fieldName.
    expect(container.querySelector(`[data-field="${name}"]`)).not.toBeNull();
    expect(screen.getByRole("heading", { name: `Label for ${name}` })).toBeInTheDocument();
    if (expectIdentical) {
      expect(screen.getByTestId("resolver-identical")).toBeInTheDocument();
    } else {
      // Both radio choices should render with their respective labels.
      expect(screen.getByLabelText(/Keep primary/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Keep candidate/i)).toBeInTheDocument();
    }
  });

  it("invokes onChange with 'right' when candidate radio clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      withProviders(
        <ConflictResolver
          fieldName="birth_date"
          fieldLabel="Date of birth"
          leftValue="1850-01-01"
          rightValue="1851-03-12"
          selected={null}
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByLabelText(/Keep candidate/i));
    expect(onChange).toHaveBeenCalledWith("right" satisfies ResolverSide);
  });

  it("renders note textarea only when onNoteChange is provided", () => {
    const { rerender } = render(
      withProviders(
        <ConflictResolver
          fieldName="sex"
          fieldLabel="Sex"
          leftValue="M"
          rightValue="F"
          selected={null}
          onChange={() => {}}
        />,
      ),
    );
    expect(screen.queryByTestId("resolver-note")).toBeNull();

    rerender(
      withProviders(
        <ConflictResolver
          fieldName="sex"
          fieldLabel="Sex"
          leftValue="M"
          rightValue="F"
          selected={null}
          onChange={() => {}}
          onNoteChange={() => {}}
        />,
      ),
    );
    expect(screen.getByTestId("resolver-note")).toBeInTheDocument();
  });

  it("hides note textarea when values are identical", () => {
    render(
      withProviders(
        <ConflictResolver
          fieldName="surname"
          fieldLabel="Surname"
          leftValue="Smith"
          rightValue="Smith"
          selected={null}
          onChange={() => {}}
          onNoteChange={() => {}}
        />,
      ),
    );
    expect(screen.queryByTestId("resolver-note")).toBeNull();
  });

  it("renders in Russian locale without missing-key fallbacks", () => {
    const { container } = render(
      withProviders(
        <ConflictResolver
          fieldName="birth_date"
          fieldLabel="Дата рождения"
          leftValue="1850"
          rightValue="1851"
          selected={null}
          onChange={() => {}}
          onNoteChange={() => {}}
        />,
        "ru",
      ),
    );
    expect(container.innerHTML).not.toMatch(/\[missing:/i);
    expect(screen.getByLabelText(/Оставить основную/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Оставить кандидата/)).toBeInTheDocument();
  });
});
