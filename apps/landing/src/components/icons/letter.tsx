import { useId } from "react";

/**
 * Brand letter (envelope) icon — 3D modern по DS-1 рецепту,
 * скопировано verbatim из icon #4 в `preview/brand-iconography.html`.
 *
 * Используется вместо `Mail` из lucide-react: lucide-allowlist
 * (ADR-0067 §«Enforcement» Decision D) запрещает content-iconography из
 * lucide; envelope — content, не affordance.
 *
 * Defs (`b3Cream`, `b3Gold`, `b3Spec`, `b3Ambient`) скоупятся через
 * `useId()` чтобы избежать id-collision'ов при множественном рендере на
 * странице (SSR + hydration-safe).
 */
export function LetterIcon({
  className,
  ariaLabel,
}: {
  className?: string;
  /** Если icon чисто декоративный — оставь undefined; aria-hidden=true. */
  ariaLabel?: string;
}) {
  const reactId = useId();
  // CSS селекторы / id'шки в SVG не любят `:` из useId — заменяем.
  const uid = reactId.replace(/:/g, "");
  const cream = `${uid}-cream`;
  const gold = `${uid}-gold`;
  const spec = `${uid}-spec`;
  const amb = `${uid}-amb`;

  return (
    <svg
      viewBox="0 0 100 100"
      className={className}
      role={ariaLabel ? "img" : undefined}
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
    >
      <defs>
        <radialGradient id={cream} cx="0.32" cy="0.22" r="1">
          <stop offset="0" stopColor="#FFFCEC" />
          <stop offset="0.2" stopColor="#FFEFC4" />
          <stop offset="0.6" stopColor="#D9A968" />
          <stop offset="0.88" stopColor="#5C3712" />
          <stop offset="1" stopColor="#1F0E02" />
        </radialGradient>
        <radialGradient id={gold} cx="0.32" cy="0.22" r="1">
          <stop offset="0" stopColor="#FFFCEC" />
          <stop offset="0.18" stopColor="#FFE39C" />
          <stop offset="0.55" stopColor="#E8B038" />
          <stop offset="0.85" stopColor="#7A4A06" />
          <stop offset="1" stopColor="#2C1700" />
        </radialGradient>
        <radialGradient id={spec} cx="0.3" cy="0.2" r="0.5">
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.95" />
          <stop offset="0.55" stopColor="#FFFFFF" stopOpacity="0.25" />
          <stop offset="1" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={amb} cx="0.7" cy="0.85" r="0.5">
          <stop offset="0" stopColor="#7DB3F2" stopOpacity="0.55" />
          <stop offset="1" stopColor="#7DB3F2" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="50" cy="85" rx="32" ry="3.5" fill="#000" opacity="0.16" />
      <rect x="14" y="30" width="72" height="44" rx="6" fill={`url(#${cream})`} />
      <path d="M14 30 L50 60 L86 30 L86 38 L50 68 L14 38 Z" fill="#3A1F0A" opacity="0.32" />
      <path d="M14 30 Q50 56 86 30 L50 60 Z" fill={`url(#${gold})`} />
      <ellipse cx="32" cy="40" rx="16" ry="4.5" fill={`url(#${spec})`} />
      <ellipse
        cx="68"
        cy="62"
        rx="22"
        ry="7"
        fill={`url(#${amb})`}
        style={{ mixBlendMode: "screen" }}
        opacity={0.55}
      />
    </svg>
  );
}
