"use client";

import * as motion from "motion/react-client";
import type { ReactNode } from "react";

/**
 * Reusable карточка для problem/solution-секций. Иконка передаётся как
 * `ReactNode`, не как lucide-component — DS-1 (ADR-0067) форсит brand-facing
 * iconography в 3D-modern SVG язык; lucide-allowlist (addendum, Decision A)
 * не включает feature-card glyphs.
 *
 * Usage:
 * ```tsx
 * import { TreeIcon } from "@/components/icons/tree";
 *
 * <FeatureCard
 *   icon={<TreeIcon className="h-7 w-7" />}
 *   title="Evidence-based tree"
 *   description="Every fact carries a citation."
 * />
 * ```
 *
 * (До тех пор пока конкретные brand-icon компоненты не добавлены, callsite
 * может передать любой `ReactNode` — компонент рендерит его как есть.)
 */
export function FeatureCard({
  icon,
  title,
  description,
  index = 0,
  tone = "violet",
}: {
  icon: ReactNode;
  title: string;
  description: string;
  index?: number;
  tone?: "violet" | "amber" | "rose";
}) {
  const tones = {
    violet:
      "bg-[var(--color-brand-50)] text-[var(--color-brand-700)] " + "ring-[var(--color-brand-200)]",
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
    rose: "bg-rose-50 text-rose-700 ring-rose-200",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{
        duration: 0.5,
        ease: "easeOut",
        delay: index * 0.08,
      }}
      className="group relative rounded-2xl bg-[var(--color-surface)] p-8
        shadow-[var(--shadow-card)] ring-1 ring-[var(--color-border)]
        transition-all duration-300 hover:-translate-y-1
        hover:shadow-[var(--shadow-elevated)] hover:ring-[var(--color-brand-200)]"
    >
      <div
        className={`mb-6 inline-flex h-12 w-12 items-center justify-center rounded-xl
          ring-1 ${tones[tone]}`}
      >
        {icon}
      </div>

      <h3 className="font-display text-xl font-semibold text-[var(--color-ink-900)]">{title}</h3>
      <p className="mt-3 text-pretty text-[var(--color-ink-600)] leading-relaxed">{description}</p>
    </motion.div>
  );
}
