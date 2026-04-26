"use client";

import type { LucideIcon } from "lucide-react";
import * as motion from "motion/react-client";

/** Reusable карточка для problem/solution-секций с иконкой и stagger reveal. */
export function FeatureCard({
  icon: Icon,
  title,
  description,
  index = 0,
  tone = "violet",
}: {
  icon: LucideIcon;
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
        <Icon className="h-6 w-6" strokeWidth={2} />
      </div>

      <h3 className="font-display text-xl font-semibold text-[var(--color-ink-900)]">{title}</h3>
      <p className="mt-3 text-pretty text-[var(--color-ink-600)] leading-relaxed">{description}</p>
    </motion.div>
  );
}
