/**
 * Декоративный фиолетовый "orb" для hero-фона. Чистый CSS-градиент
 * с blur и плавающей анимацией (через @utility animate-float-orb).
 * Скрыт от screen readers — чисто визуальный.
 */
export function GradientOrb({
  className,
  size = "lg",
}: {
  className?: string;
  size?: "sm" | "md" | "lg";
}) {
  const sizes = {
    sm: "h-64 w-64",
    md: "h-96 w-96",
    lg: "h-[42rem] w-[42rem]",
  };

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute ${sizes[size]} ${className ?? ""}`}
    >
      <div
        className="h-full w-full rounded-full opacity-50 blur-3xl animate-float-orb"
        style={{
          background:
            "conic-gradient(from 180deg at 50% 50%, " +
            "var(--color-brand-300) 0deg, " +
            "var(--color-brand-500) 120deg, " +
            "var(--color-brand-800) 240deg, " +
            "var(--color-brand-300) 360deg)",
        }}
      />
    </div>
  );
}
