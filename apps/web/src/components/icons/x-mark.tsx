/**
 * Inline X (close) SVG для buttons / dismissible panels.
 *
 * Используется вместо unicode close glyph U+2715 (DS-1 §voice forbids emoji)
 * и вместо `X` from lucide-react (apps/web не имеет lucide в deps; добавлять
 * пакет ради одной иконки overhead). Lucide allowlist (ADR-0067 §«Enforcement»
 * Decision A) разрешает `X`, но только если lucide уже подключён —
 * inline-stroke alternative равноценен по DS-1 правилам (SKILL.md
 * §iconography: «white interior strokes for chevrons / checks are fine»;
 * X — symmetrical вариант той же категории affordance).
 */
export function XMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={className}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 4 L12 12 M12 4 L4 12" />
    </svg>
  );
}
