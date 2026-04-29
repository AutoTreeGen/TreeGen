"use client";

import { useTranslations } from "next-intl";
import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * Side-by-side resolver одного conflicting field'а в merge UI (Phase 6.4).
 *
 * UI-уровневый control, **не** изменяет backend-семантику merge'а: backend
 * всегда применяет survivor's value (см. ADR-0022 §Field-level merge policy).
 * Этот компонент собирает intent пользователя, по которому caller вычисляет
 * survivor_choice (left/right) для commit-payload и формирует human-readable
 * аудит-журнал в provenance.
 *
 * Поддерживаемая ``side`` — ``"left"`` (primary) и ``"right"`` (candidate);
 * ``null`` — пользователь ещё не сделал выбор.
 */
export type ResolverSide = "left" | "right";

export type ConflictResolverProps = {
  fieldName: string;
  /** Локализованный label поля (например, "Date of birth"). */
  fieldLabel: string;
  leftValue: unknown;
  rightValue: unknown;
  /** Текущий выбор пользователя — null = ещё не выбрал. */
  selected: ResolverSide | null;
  onChange: (next: ResolverSide) => void;
  /** Опциональная заметка пользователя о причине выбора. */
  note?: string;
  onNoteChange?: (next: string) => void;
  /** Если true — формула «значения совпали»; диффа нет. */
  identical?: boolean;
  className?: string;
};

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function ConflictResolver({
  fieldName,
  fieldLabel,
  leftValue,
  rightValue,
  selected,
  onChange,
  note,
  onNoteChange,
  identical = false,
  className,
}: ConflictResolverProps) {
  const t = useTranslations("persons.merge.resolver");
  const groupId = useId();
  const noteId = useId();

  const leftDisplay = formatValue(leftValue);
  const rightDisplay = formatValue(rightValue);
  const valuesIdentical = identical || leftDisplay === rightDisplay;

  return (
    <fieldset
      aria-labelledby={`${groupId}-label`}
      data-field={fieldName}
      className={cn(
        "rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4",
        className,
      )}
    >
      <legend className="sr-only">{fieldLabel}</legend>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3
          id={`${groupId}-label`}
          className="text-sm font-semibold text-[color:var(--color-ink-900)]"
        >
          {fieldLabel}
        </h3>
        <span className="font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-ink-500)]">
          {fieldName}
        </span>
      </div>

      {valuesIdentical ? (
        <p
          data-testid="resolver-identical"
          className="rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-900 ring-1 ring-emerald-200"
        >
          {t("identical")}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <ResolverChoice
            side="left"
            label={t("keepLeft")}
            value={leftDisplay}
            checked={selected === "left"}
            name={groupId}
            onSelect={() => onChange("left")}
            highlightDiff
          />
          <ResolverChoice
            side="right"
            label={t("keepRight")}
            value={rightDisplay}
            checked={selected === "right"}
            name={groupId}
            onSelect={() => onChange("right")}
            highlightDiff
          />
        </div>
      )}

      {onNoteChange && !valuesIdentical ? (
        <div className="mt-3">
          <label htmlFor={noteId} className="block text-xs text-[color:var(--color-ink-500)]">
            {t("noteLabel")}
          </label>
          <textarea
            id={noteId}
            data-testid="resolver-note"
            value={note ?? ""}
            onChange={(event) => onNoteChange(event.target.value)}
            placeholder={t("notePlaceholder")}
            rows={2}
            className={cn(
              "mt-1 w-full resize-y rounded-md border border-[color:var(--color-border)]",
              "bg-[color:var(--color-surface)] px-2 py-1 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]",
            )}
          />
        </div>
      ) : null}
    </fieldset>
  );
}

function ResolverChoice({
  side,
  label,
  value,
  checked,
  name,
  onSelect,
  highlightDiff,
}: {
  side: ResolverSide;
  label: string;
  value: string;
  checked: boolean;
  name: string;
  onSelect: () => void;
  highlightDiff: boolean;
}) {
  return (
    <label
      data-side={side}
      className={cn(
        "flex cursor-pointer items-start gap-2 rounded-md border p-2 transition-colors",
        checked
          ? "border-[color:var(--color-accent)] bg-[color:var(--color-surface)] ring-1 ring-[color:var(--color-accent)]"
          : "border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)] hover:border-[color:var(--color-accent)]",
      )}
    >
      <input
        type="radio"
        name={name}
        value={side}
        checked={checked}
        onChange={onSelect}
        className="mt-1 h-4 w-4 accent-[color:var(--color-accent)]"
        aria-label={label}
      />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-[color:var(--color-ink-900)]">{label}</p>
        <p
          className={cn(
            "mt-1 break-words font-mono text-[11px]",
            highlightDiff && checked ? "text-emerald-900" : "text-[color:var(--color-ink-700)]",
          )}
        >
          {value}
        </p>
      </div>
    </label>
  );
}
