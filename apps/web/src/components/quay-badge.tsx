import { cn } from "@/lib/utils";

/**
 * GEDCOM QUAY (quality of evidence) — раскраска и человекочитаемый
 * label по уровню достоверности (см. ADR-0015 + Phase 1.x QUAY mapping):
 *
 * * 3 — primary evidence (direct, original record).
 * * 2 — secondary с надёжной cross-reference.
 * * 1 — secondary / questionable.
 * * 0 — unreliable / hearsay.
 * * null — QUAY не указан в GEDCOM (нейтральный тон).
 *
 * Совпадает с тонами `confidence-badge` в duplicate-pair-card (Phase 4.5),
 * но семантика другая — мы рендерим evidence-уровень, не algorithmic
 * confidence; формула `0..3 → 0.1/0.4/0.7/0.95` живёт в backend'е
 * (`_quay_to_confidence` в import_runner) и приходит как `quality`.
 */
export function QuayBadge({ raw }: { raw: number | null | undefined }) {
  const tone =
    raw === 3 ? "primary" : raw === 2 ? "good" : raw === 1 ? "weak" : raw === 0 ? "bad" : "unknown";
  const palette = {
    primary: "bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300",
    good: "bg-sky-100 text-sky-900 ring-1 ring-sky-300",
    weak: "bg-amber-100 text-amber-900 ring-1 ring-amber-300",
    bad: "bg-red-100 text-red-900 ring-1 ring-red-300",
    unknown:
      "bg-[color:var(--color-surface-muted)] text-[color:var(--color-ink-700)] ring-1 ring-[color:var(--color-border)]",
  } as const;
  const label =
    raw === 3
      ? "QUAY 3 · primary"
      : raw === 2
        ? "QUAY 2 · good"
        : raw === 1
          ? "QUAY 1 · weak"
          : raw === 0
            ? "QUAY 0 · unreliable"
            : "QUAY · unknown";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        palette[tone],
      )}
    >
      {label}
    </span>
  );
}
