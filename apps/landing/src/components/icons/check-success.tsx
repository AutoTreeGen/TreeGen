import { useId } from "react";

/**
 * Brand check-success (verified) icon — 3D modern по DS-1 рецепту,
 * скопировано verbatim из icon #11 в `preview/brand-iconography.html`.
 *
 * Используется вместо `CheckCircle2` из lucide-react: form success
 * states / empty-states — brand-facing per SKILL.md §iconography.
 * Lucide allowlist (ADR-0067 addendum) рассчитан на UI affordances
 * (chevrons, X, grip handles, Loader2), не на content-iconography.
 */
export function CheckSuccessIcon({
  className,
  ariaLabel,
}: {
  className?: string;
  ariaLabel?: string;
}) {
  const reactId = useId();
  const uid = reactId.replace(/:/g, "");
  const mint = `${uid}-mint`;
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
        <radialGradient id={mint} cx="0.32" cy="0.22" r="1">
          <stop offset="0" stopColor="#F0FCEF" />
          <stop offset="0.18" stopColor="#C0F0CC" />
          <stop offset="0.55" stopColor="#5DC586" />
          <stop offset="0.85" stopColor="#1F6840" />
          <stop offset="1" stopColor="#06281A" />
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
      <ellipse cx="50" cy="85" rx="26" ry="3.5" fill="#000" opacity="0.16" />
      <circle cx="50" cy="50" r="32" fill={`url(#${mint})`} />
      <path
        d="M32 52 L44 64 L68 38"
        stroke="#FFFFFF"
        strokeWidth="7.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <ellipse cx="40" cy="32" rx="14" ry="5.5" fill={`url(#${spec})`} />
      <ellipse
        cx="64"
        cy="72"
        rx="22"
        ry="7"
        fill={`url(#${amb})`}
        style={{ mixBlendMode: "screen" }}
        opacity={0.55}
      />
    </svg>
  );
}
