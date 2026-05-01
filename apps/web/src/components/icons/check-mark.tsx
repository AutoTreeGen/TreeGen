/**
 * Inline tick SVG для checked-list bullets / pricing features.
 *
 * Используется вместо unicode check glyph U+2713, который запрещён по DS-1
 * §voice anti-patterns (ADR-0067 §«Enforcement» Decision C — emoji rule).
 * SKILL.md §iconography разрешает «white interior strokes for chevrons /
 * checks» — простой stroke-based path, не 3D-modern.
 */
export function CheckMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={className}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 8.5 L7 12 L13 4.5" />
    </svg>
  );
}
